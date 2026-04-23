from __future__ import annotations

from noirdoc.detection.base import DetectedEntity
from noirdoc.pseudonymization.engine import PseudonymizationEngine
from noirdoc.pseudonymization.mapper import PseudonymMapper
from noirdoc.reidentification.engine import ReidentificationEngine


def _make_mapper() -> PseudonymMapper:
    mapper = PseudonymMapper()
    mapper.get_or_create("Max Müller", "PERSON")
    mapper.get_or_create("Berlin", "LOCATION")
    mapper.get_or_create("max@test.de", "EMAIL")
    return mapper


def test_simple_reidentification():
    mapper = _make_mapper()
    engine = ReidentificationEngine()
    text = "<<PERSON_1>> wohnt in <<LOCATION_1>>."
    result = engine.reidentify(text, mapper)
    assert result == "Max Müller wohnt in Berlin."


def test_partial_unknown_pseudonym_stays():
    mapper = _make_mapper()
    engine = ReidentificationEngine()
    text = "<<PERSON_1>> kennt <<PERSON_99>>."
    result = engine.reidentify(text, mapper)
    assert result == "Max Müller kennt <<PERSON_99>>."


def test_no_pseudonyms_in_text():
    mapper = _make_mapper()
    engine = ReidentificationEngine()
    text = "Hallo Welt, keine Pseudonyme hier."
    result = engine.reidentify(text, mapper)
    assert result == text


def test_false_positive_heading_not_matched():
    """<<HEADING>> is not a valid pseudonym pattern (no _\\d+ suffix)."""
    mapper = _make_mapper()
    engine = ReidentificationEngine()
    text = "<<HEADING>> ist kein Pseudonym, <<PERSON_1>> schon."
    result = engine.reidentify(text, mapper)
    assert result == "<<HEADING>> ist kein Pseudonym, Max Müller schon."


def test_full_roundtrip():
    """Original -> Pseudonymize -> Reidentify = Original."""
    original = "Max Müller wohnt in Berlin und ist unter max@test.de erreichbar."
    entities = [
        DetectedEntity(
            entity_type="PERSON",
            text="Max Müller",
            start=0,
            end=10,
            score=0.9,
            source="test",
        ),
        DetectedEntity(
            entity_type="LOCATION",
            text="Berlin",
            start=20,
            end=26,
            score=0.85,
            source="test",
        ),
        DetectedEntity(
            entity_type="EMAIL",
            text="max@test.de",
            start=41,
            end=52,
            score=0.95,
            source="test",
        ),
    ]
    mapper = PseudonymMapper()
    pseudo_engine = PseudonymizationEngine()
    reident_engine = ReidentificationEngine()

    pseudonymized = pseudo_engine.pseudonymize(original, entities, mapper)
    assert "Max Müller" not in pseudonymized

    reidentified = reident_engine.reidentify(pseudonymized, mapper)
    assert reidentified == original


def test_reidentify_partial_stats():
    mapper = _make_mapper()
    engine = ReidentificationEngine()
    text = "<<PERSON_1>> und <<PERSON_99>> in <<LOCATION_1>>."
    result, replaced, unresolved = engine.reidentify_partial(text, mapper)
    assert replaced == 2
    assert unresolved == 1
    assert "Max Müller" in result
    assert "<<PERSON_99>>" in result
    assert "Berlin" in result


def test_reidentify_partial_all_resolved():
    mapper = _make_mapper()
    engine = ReidentificationEngine()
    text = "<<PERSON_1>> in <<LOCATION_1>>."
    result, replaced, unresolved = engine.reidentify_partial(text, mapper)
    assert replaced == 2
    assert unresolved == 0


def test_multiple_same_pseudonym():
    mapper = _make_mapper()
    engine = ReidentificationEngine()
    text = "<<PERSON_1>> und <<PERSON_1>> nochmal."
    result = engine.reidentify(text, mapper)
    assert result == "Max Müller und Max Müller nochmal."


# ── Lenient reidentification ──────────────────────────────


def test_lenient_lowercase():
    """LLM outputs lowercase pseudonym."""
    mapper = _make_mapper()
    engine = ReidentificationEngine()
    text = "<<person_1>> wohnt in <<location_1>>."
    result = engine.reidentify(text, mapper)
    assert result == "Max Müller wohnt in Berlin."


def test_lenient_mixed_case():
    """LLM outputs mixed case pseudonym."""
    mapper = _make_mapper()
    engine = ReidentificationEngine()
    text = "<<Person_1>> wohnt in <<Location_1>>."
    result = engine.reidentify(text, mapper)
    assert result == "Max Müller wohnt in Berlin."


def test_lenient_spaces_inside_brackets():
    """LLM adds spaces inside angle brackets."""
    mapper = _make_mapper()
    engine = ReidentificationEngine()
    text = "<< PERSON_1 >> wohnt in << LOCATION_1 >>."
    result = engine.reidentify(text, mapper)
    assert result == "Max Müller wohnt in Berlin."


def test_lenient_unicode_guillemets():
    """LLM uses Unicode guillemets instead of <<>>."""
    mapper = _make_mapper()
    engine = ReidentificationEngine()
    text = "\u00abPERSON_1\u00bb wohnt in \u00abLOCATION_1\u00bb."
    result = engine.reidentify(text, mapper)
    assert result == "Max Müller wohnt in Berlin."


def test_lenient_does_not_false_match():
    """Lenient pattern must not match non-pseudonym text."""
    mapper = _make_mapper()
    engine = ReidentificationEngine()
    text = "The value << 5 >> 3 is true."
    result = engine.reidentify(text, mapper)
    assert result == text


def test_lenient_partial_stats():
    """reidentify_partial also handles lenient matches."""
    mapper = _make_mapper()
    engine = ReidentificationEngine()
    text = "<<person_1>> und <<PERSON_99>> in <<LOCATION_1>>."
    result, replaced, unresolved = engine.reidentify_partial(text, mapper)
    # <<LOCATION_1>> resolved strict, <<person_1>> resolved lenient, <<PERSON_99>> unresolved strict
    assert replaced == 2
    assert unresolved == 1
    assert "Max Müller" in result
    assert "Berlin" in result


# ── Custom label (type-blind) ────────────────────────────


def test_roundtrip_custom_label():
    """Roundtrip with type-blind pseudonyms using a custom label."""
    original = "Max Müller wohnt in Berlin und ist unter max@test.de erreichbar."
    entities = [
        DetectedEntity(
            entity_type="PERSON",
            text="Max Müller",
            start=0,
            end=10,
            score=0.9,
            source="test",
        ),
        DetectedEntity(
            entity_type="LOCATION",
            text="Berlin",
            start=20,
            end=26,
            score=0.85,
            source="test",
        ),
        DetectedEntity(
            entity_type="EMAIL",
            text="max@test.de",
            start=41,
            end=52,
            score=0.95,
            source="test",
        ),
    ]
    mapper = PseudonymMapper(label="PLACEHOLDER")
    pseudo_engine = PseudonymizationEngine()
    reident_engine = ReidentificationEngine()

    pseudonymized = pseudo_engine.pseudonymize(original, entities, mapper)
    assert "<<PLACEHOLDER_1>>" in pseudonymized
    assert "<<PLACEHOLDER_2>>" in pseudonymized
    assert "<<PLACEHOLDER_3>>" in pseudonymized
    assert "Max Müller" not in pseudonymized
    assert "PERSON" not in pseudonymized
    assert "LOCATION" not in pseudonymized

    reidentified = reident_engine.reidentify(pseudonymized, mapper)
    assert reidentified == original


def test_lenient_custom_label():
    """LLM case-changes on custom-label pseudonyms still resolve."""
    mapper = PseudonymMapper(label="PLACEHOLDER")
    mapper.get_or_create("Max Müller", "PERSON")
    engine = ReidentificationEngine()
    text = "<<placeholder_1>> wohnt hier."
    result = engine.reidentify(text, mapper)
    assert result == "Max Müller wohnt hier."
