from __future__ import annotations

import json

from noirdoc.pseudonymization.mapper import PseudonymMapper


def test_consistent_pseudonym():
    mapper = PseudonymMapper()
    p1 = mapper.get_or_create("Max Müller", "PERSON")
    p2 = mapper.get_or_create("Max Müller", "PERSON")
    assert p1 == "<<PERSON_1>>"
    assert p1 == p2


def test_case_insensitive():
    mapper = PseudonymMapper()
    mapper.get_or_create("Max Müller", "PERSON")
    p2 = mapper.get_or_create("max müller", "PERSON")
    assert p2 == "<<PERSON_1>>"


def test_different_entities_get_different_pseudonyms():
    mapper = PseudonymMapper()
    p1 = mapper.get_or_create("Max Müller", "PERSON")
    p2 = mapper.get_or_create("Lisa Schmidt", "PERSON")
    p3 = mapper.get_or_create("max@test.de", "EMAIL")
    assert p1 == "<<PERSON_1>>"
    assert p2 == "<<PERSON_2>>"
    assert p3 == "<<EMAIL_1>>"


def test_reverse_lookup():
    mapper = PseudonymMapper()
    mapper.get_or_create("Max Müller", "PERSON")
    assert mapper.reverse_lookup("<<PERSON_1>>") == "Max Müller"
    assert mapper.reverse_lookup("<<UNKNOWN_1>>") is None


def test_mapping_summary():
    mapper = PseudonymMapper()
    mapper.get_or_create("Max Müller", "PERSON")
    mapper.get_or_create("Lisa Schmidt", "PERSON")
    mapper.get_or_create("max@test.de", "EMAIL")
    summary = mapper.get_mapping_summary()
    assert summary == {
        "<<PERSON_1>>": "Max Müller",
        "<<PERSON_2>>": "Lisa Schmidt",
        "<<EMAIL_1>>": "max@test.de",
    }


def test_counts_summary():
    mapper = PseudonymMapper()
    mapper.get_or_create("Max Müller", "PERSON")
    mapper.get_or_create("Lisa Schmidt", "PERSON")
    mapper.get_or_create("Max Müller", "PERSON")  # dedup, no recount
    mapper.get_or_create("max@test.de", "EMAIL")
    summary = mapper.get_counts_summary()
    assert summary == {
        "total_entities": 3,
        "by_type": {"PERSON": 2, "EMAIL": 1},
    }
    assert "Max Müller" not in json.dumps(summary)


def test_get_all_pseudonyms():
    mapper = PseudonymMapper()
    mapper.get_or_create("Max Müller", "PERSON")
    mapper.get_or_create("Berlin", "LOCATION")
    pseudonyms = mapper.get_all_pseudonyms()
    assert "<<PERSON_1>>" in pseudonyms
    assert "<<LOCATION_1>>" in pseudonyms
    assert len(pseudonyms) == 2


def test_entity_count():
    mapper = PseudonymMapper()
    assert mapper.entity_count == 0
    mapper.get_or_create("Max Müller", "PERSON")
    assert mapper.entity_count == 1
    mapper.get_or_create("Max Müller", "PERSON")  # duplicate
    assert mapper.entity_count == 1
    mapper.get_or_create("Lisa Schmidt", "PERSON")
    assert mapper.entity_count == 2


def test_original_case_preserved_in_reverse():
    mapper = PseudonymMapper()
    mapper.get_or_create("Max Müller", "PERSON")
    # lookup via lowercase still returns original case
    assert mapper.reverse_lookup("<<PERSON_1>>") == "Max Müller"


def test_custom_prefix_suffix():
    mapper = PseudonymMapper(prefix="[", suffix="]")
    p = mapper.get_or_create("Max", "PERSON")
    assert p == "[PERSON_1]"


# ── Custom label (type-blind mode) ──────────────────────


def test_label_overrides_entity_type():
    mapper = PseudonymMapper(label="PLACEHOLDER")
    p1 = mapper.get_or_create("Max Müller", "PERSON")
    p2 = mapper.get_or_create("max@test.de", "EMAIL")
    p3 = mapper.get_or_create("Berlin", "LOCATION")
    assert p1 == "<<PLACEHOLDER_1>>"
    assert p2 == "<<PLACEHOLDER_2>>"
    assert p3 == "<<PLACEHOLDER_3>>"


def test_label_global_counter():
    """All entity types share one counter when label is set."""
    mapper = PseudonymMapper(label="REDACTED")
    mapper.get_or_create("Max Müller", "PERSON")
    mapper.get_or_create("Lisa Schmidt", "PERSON")
    p = mapper.get_or_create("max@test.de", "EMAIL")
    assert p == "<<REDACTED_3>>"


def test_label_none_keeps_entity_type():
    """Default label=None preserves per-type behavior."""
    mapper = PseudonymMapper(label=None)
    p1 = mapper.get_or_create("Max Müller", "PERSON")
    p2 = mapper.get_or_create("max@test.de", "EMAIL")
    assert p1 == "<<PERSON_1>>"
    assert p2 == "<<EMAIL_1>>"


def test_label_consistent_pseudonym():
    mapper = PseudonymMapper(label="PLACEHOLDER")
    p1 = mapper.get_or_create("Max Müller", "PERSON")
    p2 = mapper.get_or_create("Max Müller", "PERSON")
    assert p1 == p2 == "<<PLACEHOLDER_1>>"
