from __future__ import annotations

from typing import NamedTuple

from noirdoc.detection.base import DetectedEntity
from noirdoc.pseudonymization.mapper import PseudonymMapper


class EmittedSpan(NamedTuple):
    """One placeholder the builder wrote into the output, and what it replaced.

    Callers that have to repeat the substitution somewhere else — DOCX runs,
    XLSX cells — need the pairing of original text and placeholder, and
    re-deriving it from entity offsets means reimplementing the overlap
    semantics below. This is that pairing, straight from the builder.

    ``original`` is the exact text the placeholder replaced: the entity's own
    text for a whole entity, the trimmed remainder slice for the part of an
    overlapping entity that was left. ``start`` and ``end`` are offsets in the
    OUTPUT text, not in the input.
    """

    original: str
    pseudonym: str
    start: int
    end: int


class _MaskedSpan(NamedTuple):
    """A stretch of input text to replace, and the original its mapping stores."""

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
        """Pseudonymize *text*; see :meth:`pseudonymize_detailed` for the semantics."""
        return self.pseudonymize_detailed(text, entities, mapper)[0]

    def pseudonymize_detailed(
        self,
        text: str,
        entities: list[DetectedEntity],
        mapper: PseudonymMapper,
    ) -> tuple[str, list[EmittedSpan]]:
        """
        Text einmal von vorne nach hinten neu aufbauen.

        Returns the pseudonymized text and one :class:`EmittedSpan` per
        placeholder written, in output order. :meth:`pseudonymize` is this
        without the second element.

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

        A remainder is trimmed before it is minted: leading and trailing
        whitespace is emitted as literal text and only the core is
        pseudonymized, so remainder mappings are shaped like detector spans,
        which never carry edge whitespace. A remainder that is nothing but
        whitespace is emitted verbatim and mints nothing — whitespace is not
        PII, and minting it would put a mapping in the way of the real value.

        Every character any entity covers is therefore replaced, whatever the
        overlap shape, and every pseudonym maps back to exactly the text it
        replaced — a remainder's mapping holds the trimmed slice, not the full
        span. For PERSON [10, 20) overlapping LOCATION [15, 25) the output
        carries the PERSON pseudonym for [10, 20) and a LOCATION pseudonym for
        text[20:25]. Non-overlapping entities take the same path they always
        did.

        Reidentification restores the input exactly, up to one designed mapper
        property that predates all of this and applies to every entity, not
        just remainders: :meth:`PseudonymMapper.get_or_create` keys on
        ``strip().lower()``, so values that differ only in case share one
        pseudonym and reveal as the spelling that was minted first ("GMBH"
        after "GmbH" reads back as "GmbH").

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
                # Partly consumed: mask what reaches past the winner, minus the
                # whitespace at its edges, which is emitted as literal text.
                remainder = text[consumed_to : entity.end]
                core = remainder.strip()
                if core:
                    core_start = consumed_to + len(remainder) - len(remainder.lstrip())
                    masked.append(
                        _MaskedSpan(
                            core_start,
                            core_start + len(core),
                            core,
                            entity.entity_type,
                        )
                    )
            consumed_to = entity.end

        pseudonyms: list[str] = [""] * len(masked)
        for i in sorted(range(len(masked)), key=lambda idx: masked[idx].start, reverse=True):
            pseudonyms[i] = mapper.get_or_create(masked[i].original, masked[i].entity_type)

        parts: list[str] = []
        emitted: list[EmittedSpan] = []
        cursor = 0
        out_len = 0
        for span, pseudonym in zip(masked, pseudonyms, strict=True):
            literal = text[cursor : span.start]
            parts.append(literal)
            out_len += len(literal)
            emitted.append(EmittedSpan(span.original, pseudonym, out_len, out_len + len(pseudonym)))
            parts.append(pseudonym)
            out_len += len(pseudonym)
            cursor = span.end
        parts.append(text[cursor:])
        return "".join(parts), emitted
