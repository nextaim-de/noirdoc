from __future__ import annotations

from typing import NamedTuple

from noirdoc.detection.base import DetectedEntity
from noirdoc.pseudonymization.mapper import PseudonymMapper


class _MaskedSpan(NamedTuple):
    """A stretch of text to replace, and the original the mapping stores for it.

    ``original`` is what a reidentification puts back, so it must be exactly
    the text this span replaces: the entity's own text for a whole entity, the
    remainder slice for the part of an overlapping entity that is left.
    """

    start: int
    end: int
    original: str
    entity_type: str


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

        Overlapping entities are masked in pieces. The ensemble deliberately
        keeps dual-type overlaps (`_merge_entities` rule 4), and they cannot
        both be substituted whole — the second substitution would land inside
        the first pseudonym and corrupt it. The span that starts first wins
        and is replaced entirely; an entity reaching past it contributes its
        remainder, the slice `text[consumed_to:entity.end]`, which gets its
        own pseudonym under that entity's type. An entity that lies inside the
        consumed span has no remainder and is skipped whole, including its
        mapping, so no pseudonym is minted for a value that never reaches the
        output.

        Every character any entity covers is therefore replaced, whatever the
        overlap shape, and every pseudonym maps back to exactly the text it
        replaced — a remainder's mapping holds the slice, not the full span.
        For PERSON [10, 20) overlapping LOCATION [15, 25) the output carries
        the PERSON pseudonym for [10, 20) and a LOCATION pseudonym for
        text[20:25]. Non-overlapping entities take the same path they always
        did.

        Pseudonyms are minted back-to-front, in the order the previous
        implementation used, remainders included: a remainder is minted in the
        slot its entity would have had. The mapper's counters are
        order-sensitive and callers persist the mapping, so the numbering must
        not shift.
        """
        masked: list[_MaskedSpan] = []
        consumed_to = 0
        for entity in sorted(entities, key=lambda e: e.start):
            if entity.start >= consumed_to:
                # Nothing has eaten into it: mask the whole entity.
                masked.append(
                    _MaskedSpan(entity.start, entity.end, entity.text, entity.entity_type)
                )
            elif entity.end <= consumed_to:
                # Consumed whole: no remainder, and no mapping either.
                continue
            else:
                # Partly consumed: mask what reaches past the winner.
                masked.append(
                    _MaskedSpan(
                        consumed_to,
                        entity.end,
                        text[consumed_to : entity.end],
                        entity.entity_type,
                    )
                )
            consumed_to = entity.end

        pseudonyms: list[str] = [""] * len(masked)
        for i in sorted(range(len(masked)), key=lambda idx: masked[idx].start, reverse=True):
            pseudonyms[i] = mapper.get_or_create(masked[i].original, masked[i].entity_type)

        parts: list[str] = []
        cursor = 0
        for span, pseudonym in zip(masked, pseudonyms, strict=True):
            parts.append(text[cursor : span.start])
            parts.append(pseudonym)
            cursor = span.end
        parts.append(text[cursor:])
        return "".join(parts)
