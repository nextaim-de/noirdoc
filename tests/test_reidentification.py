from __future__ import annotations

from noirdoc.detection.base import DetectedEntity
from noirdoc.mappings.hydration import hydrate_mapper
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


# ── Round-trip over overlapping spans ─────────────────────
#
# Overlaps are masked in two pieces: the span that starts first is replaced
# whole, and what is left of the loser is replaced under its own mapping. Both
# pieces are ordinary mappings, so reidentification restores the original text
# character for character.

_RULER = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"


def _ent(entity_type: str, text: str, start: int, end: int) -> DetectedEntity:
    return DetectedEntity(
        entity_type=entity_type,
        text=text,
        start=start,
        end=end,
        score=0.9,
        source="test",
    )


def test_roundtrip_overlapping_remainder():
    """PERSON [10, 20) overlapping LOCATION [15, 25): both pieces come back."""
    entities = [
        _ent("PERSON", _RULER[10:20], 10, 20),
        _ent("LOCATION", _RULER[15:25], 15, 25),
    ]
    mapper = PseudonymMapper()
    pseudonymized = PseudonymizationEngine().pseudonymize(_RULER, entities, mapper)

    assert pseudonymized == "0123456789<<PERSON_1>><<LOCATION_1>>PQRSTUVWXYZ"
    assert ReidentificationEngine().reidentify(pseudonymized, mapper) == _RULER


def test_roundtrip_overlap_chain():
    """Three chained spans, two of them reduced to remainders."""
    entities = [
        _ent("PERSON", _RULER[0:10], 0, 10),
        _ent("LOCATION", _RULER[5:15], 5, 15),
        _ent("EMAIL", _RULER[8:20], 8, 20),
    ]
    mapper = PseudonymMapper()
    pseudonymized = PseudonymizationEngine().pseudonymize(_RULER, entities, mapper)

    assert pseudonymized == "<<PERSON_1>><<LOCATION_1>><<EMAIL_1>>KLMNOPQRSTUVWXYZ"
    assert ReidentificationEngine().reidentify(pseudonymized, mapper) == _RULER


def test_roundtrip_mixed_document_with_an_overlap():
    """A document mixing plain entities with one overlapping pair.

    The second "Weber" is flagged both as a PERSON and as the head of an
    ORGANIZATION. PERSON wins the shared start, so the organization is masked
    as its remainder " GmbH" — and the reveal puts the whole name back.
    """
    original = "Anna Weber wohnt in Berlin und arbeitet bei Weber GmbH in Hamburg."
    org_start = original.index("Weber GmbH")
    entities = [
        _ent("PERSON", "Anna Weber", 0, 10),
        _ent("LOCATION", "Berlin", original.index("Berlin"), original.index("Berlin") + 6),
        _ent("PERSON", "Weber", org_start, org_start + 5),
        _ent("ORGANIZATION", "Weber GmbH", org_start, org_start + 10),
        _ent(
            "LOCATION",
            "Hamburg",
            original.index("Hamburg"),
            original.index("Hamburg") + 7,
        ),
    ]
    mapper = PseudonymMapper()
    pseudonymized = PseudonymizationEngine().pseudonymize(original, entities, mapper)

    assert "Weber" not in pseudonymized
    assert "GmbH" not in pseudonymized
    assert "Berlin" not in pseudonymized
    assert "Hamburg" not in pseudonymized
    assert mapper.reverse_lookup("<<ORGANIZATION_1>>") == " GmbH"

    assert ReidentificationEngine().reidentify(pseudonymized, mapper) == original

    # The same holds for a mapper rebuilt from the persisted mapping dict —
    # remainders are stored and restored like any other pseudonym.
    rehydrated = hydrate_mapper(mapper.get_mapping_summary())
    assert ReidentificationEngine().reidentify(pseudonymized, rehydrated) == original


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
    _result, replaced, unresolved = engine.reidentify_partial(text, mapper)
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
