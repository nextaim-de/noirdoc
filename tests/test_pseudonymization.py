from __future__ import annotations

from noirdoc.detection.base import DetectedEntity
from noirdoc.pseudonymization.engine import PseudonymizationEngine
from noirdoc.pseudonymization.mapper import PseudonymMapper
from noirdoc.reidentification.engine import ReidentificationEngine


def _ent(entity_type: str, text: str, start: int, end: int) -> DetectedEntity:
    return DetectedEntity(
        entity_type=entity_type,
        text=text,
        start=start,
        end=end,
        score=0.9,
        source="test",
    )


def test_multiple_entities():
    text = "Max Müller wohnt in Berlin und ist unter max.mueller@test.de erreichbar."
    entities = [
        _ent("PERSON", "Max Müller", 0, 10),
        _ent("LOCATION", "Berlin", 20, 26),
        _ent("EMAIL", "max.mueller@test.de", 41, 60),
    ]
    mapper = PseudonymMapper()
    engine = PseudonymizationEngine()
    result = engine.pseudonymize(text, entities, mapper)
    assert "<<PERSON_1>>" in result
    assert "<<LOCATION_1>>" in result
    assert "<<EMAIL_1>>" in result
    assert "Max Müller" not in result
    assert "Berlin" not in result
    assert "max.mueller@test.de" not in result


def test_consistent_pseudonyms_same_name():
    text = "Max Müller und Lisa Schmidt kennen Max Müller."
    entities = [
        _ent("PERSON", "Max Müller", 0, 10),
        _ent("PERSON", "Lisa Schmidt", 15, 27),
        _ent("PERSON", "Max Müller", 35, 45),
    ]
    mapper = PseudonymMapper()
    engine = PseudonymizationEngine()
    result = engine.pseudonymize(text, entities, mapper)
    assert result == "<<PERSON_1>> und <<PERSON_2>> kennen <<PERSON_1>>."


def test_empty_text():
    mapper = PseudonymMapper()
    engine = PseudonymizationEngine()
    assert engine.pseudonymize("", [], mapper) == ""


def test_no_entities():
    mapper = PseudonymMapper()
    engine = PseudonymizationEngine()
    assert engine.pseudonymize("Hallo Welt", [], mapper) == "Hallo Welt"


def test_single_entity():
    text = "Hallo Max!"
    entities = [_ent("PERSON", "Max", 6, 9)]
    mapper = PseudonymMapper()
    engine = PseudonymizationEngine()
    result = engine.pseudonymize(text, entities, mapper)
    assert result == "Hallo <<PERSON_1>>!"


def test_offsets_preserved_with_replacement():
    """Replacing back-to-front ensures offsets stay correct."""
    text = "AB CD EF"
    entities = [
        _ent("PERSON", "AB", 0, 2),
        _ent("PERSON", "EF", 6, 8),
    ]
    mapper = PseudonymMapper()
    engine = PseudonymizationEngine()
    result = engine.pseudonymize(text, entities, mapper)
    assert "<<PERSON_1>>" in result
    assert "<<PERSON_2>>" in result
    assert "CD" in result


# --- Characterization of pseudonymize ---

# A fixed ruler string: index 10 is "A", index 15 is "F", index 25 is "P".
_RULER = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


class RecordingMapper(PseudonymMapper):
    """PseudonymMapper that records every get_or_create call in order."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str]] = []

    def get_or_create(self, entity_text: str, entity_type: str) -> str:
        self.calls.append((entity_text, entity_type))
        return super().get_or_create(entity_text, entity_type)


def test_pseudonymize_char_mapping_assigned_back_to_front():
    """Pseudonyms are assigned in DESCENDING start order.

    The counters are order-sensitive, so the last entity in the text gets
    `_1`. This is part of the public call pattern: the gateway persists
    mappings, so the numbering must not change.

    A masked remainder is minted in the losing entity's own slot: it replaces
    the loser in the same descending walk, so an overlapping pair numbers
    exactly like a non-overlapping one.
    """
    text = "Anna und Bert"
    entities = [_ent("PERSON", "Anna", 0, 4), _ent("PERSON", "Bert", 9, 13)]
    mapper = RecordingMapper()
    result = PseudonymizationEngine().pseudonymize(text, entities, mapper)
    assert mapper.calls == [("Bert", "PERSON"), ("Anna", "PERSON")]
    assert result == "<<PERSON_2>> und <<PERSON_1>>"

    # Same walk with an overlap: the winner [10, 20) and the remainder
    # [20, 25) of the loser [15, 25). The remainder is minted first because
    # it sits later in the text, so it takes `_1`.
    overlapping = [
        _ent("PERSON", _RULER[10:20], 10, 20),
        _ent("PERSON", _RULER[15:25], 15, 25),
    ]
    overlap_mapper = RecordingMapper()
    overlap_result = PseudonymizationEngine().pseudonymize(_RULER, overlapping, overlap_mapper)
    assert overlap_mapper.calls == [("KLMNO", "PERSON"), ("ABCDEFGHIJ", "PERSON")]
    assert overlap_result == "0123456789<<PERSON_2>><<PERSON_1>>PQRSTUVWXYZ"


def test_pseudonymize_char_input_order_does_not_matter():
    """Entities are sorted internally, so the caller's list order is irrelevant."""
    text = "Anna und Bert"
    anna = _ent("PERSON", "Anna", 0, 4)
    bert = _ent("PERSON", "Bert", 9, 13)
    forward = PseudonymizationEngine().pseudonymize(text, [anna, bert], RecordingMapper())
    reverse = PseudonymizationEngine().pseudonymize(text, [bert, anna], RecordingMapper())
    assert forward == reverse == "<<PERSON_2>> und <<PERSON_1>>"


def test_pseudonymize_char_overlapping_remainder_is_masked():
    """Overlapping entities: the first span wins, the loser's tail is masked too.

    Two overlapping entities of different types survive the merge as a
    deliberate dual-type annotation (see `_merge_entities` rule 4).

    BEFORE 0.1.3 this returned spliced garbage — the back-to-front replacement
    substituted LOCATION[15, 25) first and then spliced PERSON[10, 20) into the
    ALREADY SUBSTITUTED text, so offset 20 no longer pointed where the caller
    meant:

        "0123456789<<PERSON_1>>ATION_1>>PQRSTUVWXYZ"

    — a half-eaten placeholder that no reverse mapping can undo, plus a phantom
    LOCATION mapping whose original value is not present in the output.

    THEN the single-pass builder dropped the loser whole, which was clean but
    left its overhang in cleartext:

        "0123456789<<PERSON_1>>KLMNOPQRSTUVWXYZ"

    — "KLMNO" is text[20:25], the tail of a span the detectors flagged.

    NOW the loser contributes its remainder: PERSON consumes [10, 20), and the
    part of LOCATION beyond that, text[20:25] == "KLMNO", is pseudonymized
    under its own LOCATION mapping. Both pseudonyms are whole, the mapping's
    original text is the remainder slice — not the full LOCATION span, which
    never reaches the output — and nothing the detectors flagged survives in
    cleartext.
    """
    entities = [
        _ent("PERSON", _RULER[10:20], 10, 20),
        _ent("LOCATION", _RULER[15:25], 15, 25),
    ]
    mapper = RecordingMapper()
    result = PseudonymizationEngine().pseudonymize(_RULER, entities, mapper)

    assert result == "0123456789<<PERSON_1>><<LOCATION_1>>PQRSTUVWXYZ"
    assert mapper.calls == [("KLMNO", "LOCATION"), ("ABCDEFGHIJ", "PERSON")]
    assert mapper.entity_count == 2
    assert mapper.reverse_lookup("<<PERSON_1>>") == "ABCDEFGHIJ"
    assert mapper.reverse_lookup("<<LOCATION_1>>") == "KLMNO"


def test_pseudonymize_char_overlap_chain_masks_every_remainder():
    """A chain of overlaps composes: each span contributes what is left of it.

    PERSON [0, 10) wins outright; LOCATION [5, 15) contributes [10, 15) and
    EMAIL [8, 20) contributes [15, 20). The three pseudonyms tile [0, 20)
    exactly, so the covered region is fully masked and every mapping stores
    the slice it actually replaced.
    """
    entities = [
        _ent("PERSON", _RULER[0:10], 0, 10),
        _ent("LOCATION", _RULER[5:15], 5, 15),
        _ent("EMAIL", _RULER[8:20], 8, 20),
    ]
    mapper = RecordingMapper()
    result = PseudonymizationEngine().pseudonymize(_RULER, entities, mapper)

    assert result == "<<PERSON_1>><<LOCATION_1>><<EMAIL_1>>KLMNOPQRSTUVWXYZ"
    assert mapper.calls == [
        ("FGHIJ", "EMAIL"),
        ("ABCDE", "LOCATION"),
        ("0123456789", "PERSON"),
    ]
    assert mapper.entity_count == 3
    assert mapper.reverse_lookup("<<PERSON_1>>") == "0123456789"
    assert mapper.reverse_lookup("<<LOCATION_1>>") == "ABCDE"
    assert mapper.reverse_lookup("<<EMAIL_1>>") == "FGHIJ"


def test_pseudonymize_char_remainder_is_trimmed_before_minting():
    """A remainder's edge whitespace stays literal; only its core is minted.

    The slice left of LOCATION [0, 17) is " am Main", starting with the space
    that separates it from the winner. Minting the slice as-is would put a
    mapping under the key "am main" whose stored value carries a leading
    space, colliding with any real "am Main" entity and revealing with the
    wrong spacing. Detector spans never carry edge whitespace; remainders are
    trimmed so they do not either, and the space is emitted as plain text.
    """
    text = "Frankfurt am Main"
    entities = [
        _ent("ORGANIZATION", "Frankfurt", 0, 9),
        _ent("LOCATION", text, 0, 17),
    ]
    mapper = RecordingMapper()
    result = PseudonymizationEngine().pseudonymize(text, entities, mapper)

    assert result == "<<ORGANIZATION_1>> <<LOCATION_1>>"
    assert mapper.calls == [("am Main", "LOCATION"), ("Frankfurt", "ORGANIZATION")]
    assert mapper.reverse_lookup("<<LOCATION_1>>") == "am Main"
    assert ReidentificationEngine().reidentify(result, mapper) == text


def test_pseudonymize_char_whitespace_only_remainder_mints_nothing():
    """Whitespace is not PII: a remainder with no core is emitted verbatim."""
    text = "Max Mueller"
    entities = [
        _ent("PERSON", "Max", 0, 3),
        _ent("PERSON", "Max ", 0, 4),
    ]
    mapper = RecordingMapper()
    result = PseudonymizationEngine().pseudonymize(text, entities, mapper)

    assert result == "<<PERSON_1>> Mueller"
    assert mapper.calls == [("Max", "PERSON")]
    assert mapper.entity_count == 1
    assert ReidentificationEngine().reidentify(result, mapper) == text


def test_pseudonymize_char_case_variants_share_one_mapping():
    """Designed mapper behaviour, pinned: case-insensitive dedup, first spelling wins.

    `PseudonymMapper.get_or_create` keys on `strip().lower()`, so two entities
    whose spellings differ only in case get ONE pseudonym, and the reveal
    restores whichever spelling was minted first — here "GMBH", because
    minting runs back to front. A round-trip is therefore exact only up to
    that dedup. This predates overlap handling and applies to every entity,
    not just remainders.

    The whitespace half of the same class — a mapping keyed "gmbh" whose
    stored value is " GmbH" — is unreachable: remainders are trimmed before
    they are minted, and detector spans do not carry edge whitespace. If the
    mapper's key ever changes, this test is the one that should notice.
    """
    text = "GmbH und GMBH"
    entities = [
        _ent("ORGANIZATION", "GmbH", 0, 4),
        _ent("ORGANIZATION", "GMBH", 9, 13),
    ]
    mapper = PseudonymMapper()
    result = PseudonymizationEngine().pseudonymize(text, entities, mapper)

    assert result == "<<ORGANIZATION_1>> und <<ORGANIZATION_1>>"
    assert mapper.entity_count == 1
    assert mapper.reverse_lookup("<<ORGANIZATION_1>>") == "GMBH"
    assert ReidentificationEngine().reidentify(result, mapper) == "GMBH und GMBH"


def test_pseudonymize_char_clip_uses_the_earlier_start_not_the_longer_span():
    """The consuming span is decided by start offset alone — the first one wins.

    Nothing is left over here: the losing PERSON span lies entirely inside the
    LOCATION span that won, so there is no remainder to mask and no mapping to
    mint for it. Remainders only appear when the loser reaches beyond the
    winner's end, as in the test above.
    """
    entities = [
        _ent("LOCATION", _RULER[10:30], 10, 30),
        _ent("PERSON", _RULER[12:15], 12, 15),
    ]
    mapper = RecordingMapper()
    result = PseudonymizationEngine().pseudonymize(_RULER, entities, mapper)

    assert result == "0123456789<<LOCATION_1>>UVWXYZ"
    assert mapper.calls == [("ABCDEFGHIJKLMNOPQRST", "LOCATION")]


def test_pseudonymize_char_identical_span_dual_type_leaks_nothing():
    """The common real overlap: two detectors, one span, two labels.

    This is what a dual-type annotation usually looks like in practice — not a
    partial overlap but the exact same characters flagged as PERSON by one
    detector and as LOCATION by another. Both decision-relevant facts:

    (a) The loser is consumed whole. Its remainder is
        text[winner.end:loser.end], which is empty when the spans end
        together, so there is nothing left to mask and exactly one mapping is
        minted — unlike the partial overlap in `..._remainder_is_masked`
        above, which mints a second one for "KLMNO".
    (b) The tie is resolved by the caller's list order. Both spans start at
        10, the sort is stable, so whichever entity the caller listed first
        wins and supplies the label in the output.
    """
    as_person_first = [
        _ent("PERSON", _RULER[10:20], 10, 20),
        _ent("LOCATION", _RULER[10:20], 10, 20),
    ]
    mapper = RecordingMapper()
    result = PseudonymizationEngine().pseudonymize(_RULER, as_person_first, mapper)

    assert result == "0123456789<<PERSON_1>>KLMNOPQRSTUVWXYZ"
    assert result.endswith(_RULER[20:])  # nothing of either span survives
    assert mapper.calls == [("ABCDEFGHIJ", "PERSON")]
    assert mapper.entity_count == 1

    location_first = RecordingMapper()
    flipped = PseudonymizationEngine().pseudonymize(
        _RULER, list(reversed(as_person_first)), location_first
    )
    assert flipped == "0123456789<<LOCATION_1>>KLMNOPQRSTUVWXYZ"
    assert location_first.calls == [("ABCDEFGHIJ", "LOCATION")]


def test_pseudonymize_char_adjacent_entities_are_not_clipped():
    """Touching but non-overlapping spans (end == next start) both survive."""
    entities = [
        _ent("PERSON", _RULER[10:15], 10, 15),
        _ent("LOCATION", _RULER[15:20], 15, 20),
    ]
    mapper = RecordingMapper()
    result = PseudonymizationEngine().pseudonymize(_RULER, entities, mapper)

    assert result == "0123456789<<PERSON_1>><<LOCATION_1>>KLMNOPQRSTUVWXYZ"
    assert mapper.calls == [("FGHIJ", "LOCATION"), ("ABCDE", "PERSON")]
