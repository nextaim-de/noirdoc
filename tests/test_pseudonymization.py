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
