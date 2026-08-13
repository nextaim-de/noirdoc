from __future__ import annotations

from noirdoc.detection.base import DetectedEntity
from noirdoc.pseudonymization.engine import PseudonymizationEngine
from noirdoc.pseudonymization.mapper import PseudonymMapper


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
    """
    text = "Anna und Bert"
    entities = [_ent("PERSON", "Anna", 0, 4), _ent("PERSON", "Bert", 9, 13)]
    mapper = RecordingMapper()
    result = PseudonymizationEngine().pseudonymize(text, entities, mapper)
    assert mapper.calls == [("Bert", "PERSON"), ("Anna", "PERSON")]
    assert result == "<<PERSON_2>> und <<PERSON_1>>"


def test_pseudonymize_char_input_order_does_not_matter():
    """Entities are sorted internally, so the caller's list order is irrelevant."""
    text = "Anna und Bert"
    anna = _ent("PERSON", "Anna", 0, 4)
    bert = _ent("PERSON", "Bert", 9, 13)
    forward = PseudonymizationEngine().pseudonymize(text, [anna, bert], RecordingMapper())
    reverse = PseudonymizationEngine().pseudonymize(text, [bert, anna], RecordingMapper())
    assert forward == reverse == "<<PERSON_2>> und <<PERSON_1>>"


# A fixed ruler string: index 10 is "A", index 15 is "F", index 25 is "P".
_RULER = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def test_pseudonymize_char_overlapping_entities_are_clipped():
    """Overlapping entities: the first span wins, the loser's tail stays unmasked.

    Two overlapping entities of different types survive the merge as a
    deliberate dual-type annotation (see `_merge_entities` rule 4).

    BEFORE 0.1.3 this returned spliced garbage — the back-to-front replacement
    substituted LOCATION[15, 25) first and then spliced PERSON[10, 20) into the
    ALREADY SUBSTITUTED text, so offset 20 no longer pointed where the caller
    meant:

        "0123456789<<PERSON_1>>ATION_1>>PQRSTUVWXYZ"

    — a half-eaten placeholder that no reverse mapping can undo, plus a phantom
    LOCATION mapping whose original value is not present in the output.

    AFTER: the single-pass builder consumes [10, 20) for PERSON and skips
    LOCATION entirely, because it starts inside the consumed span. Skipped is
    skipped: `get_or_create` is not called for it, so no phantom mapping is
    minted.

    THE CAVEAT IS NOT MERELY A LOST ANNOTATION — IT IS RESIDUAL CLEARTEXT.
    The asserted output ends "...<<PERSON_1>>KLMNOPQRSTUVWXYZ", and the "KLMNO"
    in it is text[20:25]: the tail of the LOCATION span the detectors flagged,
    now emitted verbatim. The old corrupt path did not print those five
    characters, because the LOCATION substitution had consumed [15, 25). So
    this change trades "corrupt but fully masked" for "clean but partially
    unmasked" on overlapping spans. Masking the remainder slice
    text[consumed_to:entity.end] under its own mapping would give full
    coverage and stay reversible; it is deliberately out of scope here.
    """
    entities = [
        _ent("PERSON", _RULER[10:20], 10, 20),
        _ent("LOCATION", _RULER[15:25], 15, 25),
    ]
    mapper = RecordingMapper()
    result = PseudonymizationEngine().pseudonymize(_RULER, entities, mapper)

    assert result == "0123456789<<PERSON_1>>KLMNOPQRSTUVWXYZ"
    assert mapper.calls == [("ABCDEFGHIJ", "PERSON")]
    assert mapper.entity_count == 1
    assert mapper.reverse_lookup("<<PERSON_1>>") == "ABCDEFGHIJ"


def test_pseudonymize_char_clip_uses_the_earlier_start_not_the_longer_span():
    """The clip is decided by start offset alone — the first span consumes.

    No cleartext survives here: the skipped PERSON span lies entirely inside
    the LOCATION span that won. Residual cleartext only appears when the loser
    reaches beyond the winner's end, as in the test above.
    """
    entities = [
        _ent("LOCATION", _RULER[10:30], 10, 30),
        _ent("PERSON", _RULER[12:15], 12, 15),
    ]
    mapper = RecordingMapper()
    result = PseudonymizationEngine().pseudonymize(_RULER, entities, mapper)

    assert result == "0123456789<<LOCATION_1>>UVWXYZ"
    assert mapper.calls == [("ABCDEFGHIJKLMNOPQRST", "LOCATION")]


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
