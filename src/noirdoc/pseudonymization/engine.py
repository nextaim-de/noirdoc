from __future__ import annotations

from noirdoc.detection.base import DetectedEntity
from noirdoc.pseudonymization.mapper import PseudonymMapper


class PseudonymizationEngine:
    """Ersetzt erkannte Entities im Text durch Pseudonyme."""

    def pseudonymize(
        self,
        text: str,
        entities: list[DetectedEntity],
        mapper: PseudonymMapper,
    ) -> str:
        """
        Text einmal von vorne nach hinten neu aufbauen.

        One pass over the entities in start order, emitting
        ``text[cursor:start] + pseudonym`` and joining at the end: O(n) in the
        text length instead of one full string copy per entity.

        Overlapping entities are clipped. The ensemble deliberately keeps
        dual-type overlaps (`_merge_entities` rule 4), and they cannot both be
        substituted — the second substitution would land inside the first
        pseudonym and corrupt it. The span that starts first wins; an entity
        starting inside the consumed span is skipped whole, including its
        mapping, so no pseudonym is minted for a value that never reaches the
        output.

        WARNING — clipping can leave detected PII in cleartext. The skipped
        entity's characters beyond the winner's end are emitted verbatim: for
        PERSON [10, 20) overlapping LOCATION [15, 25), text[20:25] — the tail
        of the LOCATION span — appears unmasked. The previous back-to-front
        substitution did consume those characters, at the price of splicing
        one pseudonym into the middle of another and producing output no
        reverse mapping could undo. Clipping trades that corruption for a
        partial leak on overlapping spans only; non-overlapping entities are
        unaffected. Masking the remainder slice under its own mapping instead
        of dropping it would close the gap and is deliberately left for a
        follow-up (see CHANGELOG).

        Pseudonyms are minted back-to-front, in the order the previous
        implementation used. The mapper's counters are order-sensitive and
        callers persist the mapping, so the numbering must not shift.
        """
        kept: list[DetectedEntity] = []
        consumed_to = 0
        for entity in sorted(entities, key=lambda e: e.start):
            if entity.start < consumed_to:
                continue
            kept.append(entity)
            consumed_to = entity.end

        pseudonyms: list[str] = [""] * len(kept)
        for i in sorted(range(len(kept)), key=lambda i: kept[i].start, reverse=True):
            pseudonyms[i] = mapper.get_or_create(kept[i].text, kept[i].entity_type)

        parts: list[str] = []
        cursor = 0
        for entity, pseudonym in zip(kept, pseudonyms, strict=True):
            parts.append(text[cursor : entity.start])
            parts.append(pseudonym)
            cursor = entity.end
        parts.append(text[cursor:])
        return "".join(parts)
