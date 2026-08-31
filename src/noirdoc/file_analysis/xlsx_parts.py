"""Pseudonymize and reveal XLSX parts that live outside the cell grid.

``pseudonymize_xlsx_smart`` only rewrites data cells. A plain openpyxl
load→save then preserves everything else verbatim, so a redacted workbook
still leaked: ``docProps/core.xml`` (creator, lastModifiedBy, title, …),
``docProps/custom.xml``, cell comments (author + text), sheet headers/footers,
and pivot caches — whose records are a literal snapshot of the source rows, so
Excel keeps displaying the original names in the pivot even after the sheet
cells were pseudonymized.

Design: **one** slot enumerator (:func:`iter_text_slots`) feeds **both**
directions — :func:`pseudonymize_workbook_parts` and
:func:`reidentify_workbook_parts` — so the redact and reveal walkers cannot
drift apart. Every value is pseudonymized through the shared mapper (never
blanked), which keeps ``noirdoc reveal`` a full round-trip.
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

import structlog

if TYPE_CHECKING:
    from openpyxl.workbook.workbook import Workbook

    from noirdoc.detection.base import DetectedEntity
    from noirdoc.file_analysis.xlsx_inference import DetectorLike
    from noirdoc.pseudonymization.mapper import PseudonymMapper
    from noirdoc.reidentification.engine import ReidentificationEngine

logger = structlog.get_logger()

_PLACEHOLDER = re.compile(r"<<[A-Z_]+_\d+>>")

# core.xml fields that are a person (or account) by definition — never rely on NER for them.
_CORE_AUTHOR_FIELDS = ("creator", "lastModifiedBy")
# core.xml free-text fields — run through the detector like body text.
_CORE_FREE_FIELDS = ("title", "subject", "description", "keywords", "category")

SlotKind = Literal["author", "free", "pivot"]


@dataclass
class _Slot:
    """One string-bearing attribute somewhere in the workbook object model."""

    surface: str  # human-readable location, e.g. "core.creator"
    kind: SlotKind
    obj: Any  # openpyxl object holding the string
    attr: str  # attribute name on ``obj``
    field_key: tuple[int, int] | None = None  # (cache_ordinal, field_index) for pivot items

    @property
    def value(self) -> str | None:
        """Current string, or ``None`` when the slot is empty / not text."""
        raw = getattr(self.obj, self.attr, None)
        if isinstance(raw, str) and raw.strip():
            return raw
        return None

    def set(self, value: str) -> None:
        setattr(self.obj, self.attr, value)


@dataclass
class PartsResult:
    """What :func:`pseudonymize_workbook_parts` found (and, unless counting only, replaced)."""

    entity_types: dict[str, int] = field(default_factory=dict)
    classifications: dict[str, str] = field(default_factory=dict)

    @property
    def entity_count(self) -> int:
        return sum(self.entity_types.values())

    def _add(self, entity_type: str, count: int = 1) -> None:
        self.entity_types[entity_type] = self.entity_types.get(entity_type, 0) + count


def iter_text_slots(wb: Workbook) -> Iterator[_Slot]:
    """Yield every string slot outside the cell grid, in a deterministic order."""
    props = wb.properties
    for name in _CORE_AUTHOR_FIELDS:
        yield _Slot(f"core.{name}", "author", props, name)
    for name in _CORE_FREE_FIELDS:
        yield _Slot(f"core.{name}", "free", props, name)


def _author_entity_type(value: str) -> str:
    return "EMAIL" if "@" in value else "PERSON"


async def _detect_all(
    texts: list[str], detector: DetectorLike, language: str
) -> list[list[DetectedEntity]]:
    sem = asyncio.Semaphore(8)

    async def _one(text: str) -> list[DetectedEntity]:
        async with sem:
            return await detector.detect(text, language)

    return await asyncio.gather(*[_one(t) for t in texts])


async def pseudonymize_workbook_parts(
    wb: Workbook,
    detector: DetectorLike,
    mapper: PseudonymMapper,
    language: str,
    *,
    apply: bool = True,
) -> PartsResult:
    """Pseudonymize every part-level string slot of *wb* in place.

    With ``apply=False`` (detect-only / block modes) entities are detected and
    counted but nothing is written and the mapper is never touched — the proxy
    shares one mapper between message text and files.
    """
    from noirdoc.pseudonymization.engine import PseudonymizationEngine

    result = PartsResult()
    slots = [s for s in iter_text_slots(wb) if s.value is not None]

    free_slots = [s for s in slots if s.kind == "free"]
    free_entities = await _detect_all(
        [s.value for s in free_slots if s.value is not None], detector, language
    )
    engine = PseudonymizationEngine()

    for slot in slots:
        value = slot.value
        if value is None:
            continue
        if slot.kind == "author":
            if mapper.reverse_lookup(value) is not None:
                continue  # already one of this mapper's placeholders
            entity_type = _author_entity_type(value)
            if apply:
                slot.set(mapper.get_or_create(value, entity_type))
            result._add(entity_type)
            result.classifications[slot.surface] = f"{entity_type} (forced)"
        elif slot.kind == "free":
            entities = free_entities[free_slots.index(slot)]
            if not entities:
                continue
            if apply:
                slot.set(engine.pseudonymize(value, entities, mapper))
            for entity in entities:
                result._add(entity.entity_type)
            best = max(entities, key=lambda e: e.score)
            result.classifications[slot.surface] = f"{best.entity_type} (detected)"

    logger.debug("xlsx_parts.completed", entity_types=result.entity_types, apply=apply)
    return result


def reidentify_workbook_parts(
    wb: Workbook, engine: ReidentificationEngine, mapper: PseudonymMapper
) -> bool:
    """Reverse placeholders in every part-level slot. Returns ``True`` if anything changed."""
    changed = False
    for slot in iter_text_slots(wb):
        value = slot.value
        if value is None or not _PLACEHOLDER.search(value):
            continue
        revealed = engine.reidentify(value, mapper)
        if revealed != value:
            slot.set(revealed)
            changed = True
    return changed
