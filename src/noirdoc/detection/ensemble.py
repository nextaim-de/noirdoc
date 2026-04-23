from __future__ import annotations

import asyncio
import re

from noirdoc.detection.base import BaseDetector, DetectedEntity

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
            *(d.detect(text, language) for d in self.detectors),
            return_exceptions=True,
        )

        all_entities: list[DetectedEntity] = []
        for result in results:
            if isinstance(result, BaseException):
                continue
            all_entities.extend(result)

        filtered = [
            e
            for e in all_entities
            if e.score >= _TYPE_THRESHOLDS.get(e.entity_type, self.score_threshold)
        ]
        merged = self._merge_entities(filtered)
        validated = [e for e in merged if _validate_person(e)]
        return sorted(validated, key=lambda e: e.start)

    def _merge_entities(self, entities: list[DetectedEntity]) -> list[DetectedEntity]:
        """
        Overlap Resolution:
        1. Sort by start, then by span length descending (longer first)
        2. For each entity, check overlap with already accepted entities
        3. On overlap with SAME type: higher score wins; tie → longer span; tie → presidio wins
        4. On overlap with DIFFERENT type: keep both (dual-type annotation)
        5. No overlap: accept entity
        """
        sorted_ents = sorted(
            entities,
            key=lambda e: (e.start, -(e.end - e.start)),
        )

        accepted: list[DetectedEntity] = []
        for candidate in sorted_ents:
            same_type_idx = None
            for i, existing in enumerate(accepted):
                if candidate.start < existing.end and existing.start < candidate.end:
                    if candidate.entity_type == existing.entity_type:
                        same_type_idx = i
                        break

            if same_type_idx is None:
                accepted.append(candidate)
            else:
                existing = accepted[same_type_idx]
                winner = self._pick_winner(existing, candidate)
                accepted[same_type_idx] = winner

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
