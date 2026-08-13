from __future__ import annotations

import asyncio
import re

import structlog

from noirdoc.detection.base import BaseDetector, DetectedEntity

log = structlog.get_logger(__name__)

# Strong indicators: if ANY of these appear in a multi-word PERSON entity, reject it.
_PERSON_STRONG_REJECT: set[str] = {
    # verbs / participles commonly absorbed by spaCy NER
    "wohnhaft",
    "verheiratet",
    "geschieden",
    "ledig",
    "verwitwet",
    "überwiesen",
    "geboren",
    "verstorben",
    "beschäftigt",
    "gemeldet",
    # common nouns that get falsely tagged
    "euro",
    "straße",
    "strasse",
    "nummer",
    "datum",
}

# Weak indicators: reject only if entity text ENDS with one of these
# (handles boundary absorption like "Hoffmann, und").
_PERSON_TRAILING_REJECT: set[str] = {
    "und",
    "oder",
    "mit",
    "für",
    "bei",
    "nach",
    "der",
    "die",
    "das",
    "ein",
    "eine",
    "er",
    "sie",
    "es",
}

# Pattern for username-like strings (lowercase + digits, no spaces)
_USERNAME_PATTERN = re.compile(r"^[a-z0-9._@\-]+$")


def _validate_person(entity: DetectedEntity) -> bool:
    """Return False if a PERSON entity is likely a false positive."""
    if entity.entity_type != "PERSON":
        return True

    text = entity.text

    # Reject entities containing commas or newlines (spaCy boundary absorption)
    if "," in text or "\n" in text:
        return False

    # Reject single-character entities
    if len(text) <= 1:
        return False

    # Reject username-like patterns (e.g. "cthwhdbfio0854", "al-kurdy8")
    if _USERNAME_PATTERN.match(text):
        return False

    # Multi-word validation
    words = text.lower().split()
    if len(words) > 1:
        if any(w in _PERSON_STRONG_REJECT for w in words):
            return False
        if words[-1] in _PERSON_TRAILING_REJECT:
            return False

    return True


# Per-entity-type score thresholds (higher = stricter, fewer FPs).
# Types not listed here use the global default.
_TYPE_THRESHOLDS: dict[str, float] = {
    "URL": 0.6,
    "DATE": 0.7,
}

# Above this many entities the merge is handed to a worker thread so it cannot
# stall the caller's event loop — the library is used in-process by an async
# gateway. Below it the thread hop costs more than the merge itself.
_MERGE_THREAD_THRESHOLD = 64


class EnsembleDetector:
    """Kombiniert mehrere Detektoren und löst Überlappungen auf."""

    def __init__(
        self,
        detectors: list[BaseDetector],
        score_threshold: float = 0.5,
    ) -> None:
        self.detectors = detectors
        self.score_threshold = score_threshold

    async def detect(self, text: str, language: str = "de") -> list[DetectedEntity]:
        if not text:
            return []

        results = await asyncio.gather(
            *(self._run_one(d, text, language) for d in self.detectors),
        )

        all_entities: list[DetectedEntity] = []
        for result in results:
            all_entities.extend(result)

        filtered = [
            e
            for e in all_entities
            if e.score >= _TYPE_THRESHOLDS.get(e.entity_type, self.score_threshold)
        ]
        if len(filtered) > _MERGE_THREAD_THRESHOLD:
            validated = await asyncio.to_thread(self._merge_and_validate, filtered)
        else:
            validated = self._merge_and_validate(filtered)
        return sorted(validated, key=lambda e: e.start)

    def _merge_and_validate(self, entities: list[DetectedEntity]) -> list[DetectedEntity]:
        """Resolve overlaps, then drop PERSON false positives.

        The two steps are one unit so the whole CPU-bound tail of `detect` can
        move to a worker thread in a single hop.
        """
        return [e for e in self._merge_entities(entities) if _validate_person(e)]

    @staticmethod
    async def _run_one(
        detector: BaseDetector,
        text: str,
        language: str,
    ) -> list[DetectedEntity]:
        """Run one detector. On failure, log and degrade to empty results.

        A silent ``return_exceptions=True`` would bury detector failures
        and cause silent leakage (e.g. PERSON detection going dark on a
        spaCy load error). We log explicitly so operators can spot the
        degraded state.
        """
        try:
            return await detector.detect(text, language)
        except Exception as exc:
            log.warning(
                "detection.detector_failed",
                detector=getattr(detector, "name", detector.__class__.__name__),
                language=language,
                error=str(exc),
            )
            return []

    def _merge_entities(self, entities: list[DetectedEntity]) -> list[DetectedEntity]:
        """
        Overlap Resolution:
        1. Sort by start, then by span length descending (longer first)
        2. For each entity, check overlap with the open slot of its own type
        3. On overlap with SAME type: higher score wins; tie → longer span; tie → presidio wins
        4. On overlap with DIFFERENT type: keep both (dual-type annotation)
        5. No overlap: accept entity

        Sort + sweep, O(n log n). Candidates arrive in start order, which means
        the accepted spans of any one type are pairwise disjoint and ordered:
        once a second span of a type is accepted, the earlier ones end at or
        before its start and can never overlap a later candidate again. So only
        the most recently accepted span per type — its "open slot" — has to be
        checked, instead of rescanning the whole accepted list (O(n²)).

        Zero-length spans satisfy neither half of the overlap test. They can
        never be replaced and never shadow the preceding span of their type, so
        they are accepted without becoming the open slot.

        The result is identical to the pre-0.1.3 nested-loop implementation,
        entity for entity and in the same order; the two are pinned to each
        other on randomized inputs by ``tests/test_merge_equivalence.py``.
        """
        sorted_ents = sorted(
            entities,
            key=lambda e: (e.start, -(e.end - e.start)),
        )

        accepted: list[DetectedEntity] = []
        # entity_type -> index in `accepted` of that type's last non-empty span
        open_slot: dict[str, int] = {}
        for candidate in sorted_ents:
            slot = open_slot.get(candidate.entity_type)
            if slot is not None:
                existing = accepted[slot]
                if candidate.start < existing.end and existing.start < candidate.end:
                    accepted[slot] = self._pick_winner(existing, candidate)
                    continue

            accepted.append(candidate)
            if candidate.end > candidate.start:
                open_slot[candidate.entity_type] = len(accepted) - 1

        return accepted

    @staticmethod
    def _pick_winner(a: DetectedEntity, b: DetectedEntity) -> DetectedEntity:
        if a.score != b.score:
            return a if a.score > b.score else b
        len_a = a.end - a.start
        len_b = b.end - b.start
        if len_a != len_b:
            return a if len_a > len_b else b
        # Tie-break: presidio wins
        if a.source == "presidio":
            return a
        if b.source == "presidio":
            return b
        return a
