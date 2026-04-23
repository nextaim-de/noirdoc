from __future__ import annotations

from noirdoc.mappings.hydration import hydrate_mapper
from noirdoc.pseudonymization.mapper import PseudonymMapper

# -- Basis-Hydration --


def test_hydrate_basic():
    mappings = {
        "<<PERSON_1>>": "Max Müller",
        "<<EMAIL_1>>": "max@test.de",
    }
    mapper = hydrate_mapper(mappings)
    assert mapper.entity_count == 2
    assert mapper.reverse_lookup("<<PERSON_1>>") == "Max Müller"
    assert mapper.reverse_lookup("<<EMAIL_1>>") == "max@test.de"


def test_hydrate_preserves_original_case():
    mappings = {"<<PERSON_1>>": "Max Müller"}
    mapper = hydrate_mapper(mappings)
    assert mapper.reverse_lookup("<<PERSON_1>>") == "Max Müller"


# -- Counter-Rekonstruktion --


def test_hydrate_counter_continues():
    """New entities get the next counter."""
    mappings = {
        "<<PERSON_1>>": "Max Müller",
        "<<PERSON_2>>": "Lisa Schmidt",
    }
    mapper = hydrate_mapper(mappings)
    pseudo = mapper.get_or_create("Anna Weber", "PERSON")
    assert pseudo == "<<PERSON_3>>"


def test_hydrate_counter_with_gaps():
    """Counter gaps are handled correctly."""
    mappings = {
        "<<PERSON_1>>": "Max",
        "<<PERSON_5>>": "Lisa",
    }
    mapper = hydrate_mapper(mappings)
    pseudo = mapper.get_or_create("Anna", "PERSON")
    assert pseudo == "<<PERSON_6>>"


def test_hydrate_multiple_types():
    mappings = {
        "<<PERSON_1>>": "Max",
        "<<EMAIL_1>>": "max@test.de",
        "<<PERSON_2>>": "Lisa",
        "<<PHONE_1>>": "0171-123",
    }
    mapper = hydrate_mapper(mappings)
    assert mapper.get_or_create("Anna", "PERSON") == "<<PERSON_3>>"
    assert mapper.get_or_create("anna@test.de", "EMAIL") == "<<EMAIL_2>>"
    assert mapper.get_or_create("0172-456", "PHONE") == "<<PHONE_2>>"


# -- Existing entities are recognized --


def test_hydrate_existing_entity_reused():
    """get_or_create for an already loaded name returns the same pseudonym."""
    mappings = {"<<PERSON_1>>": "Max Müller"}
    mapper = hydrate_mapper(mappings)
    pseudo = mapper.get_or_create("Max Müller", "PERSON")
    assert pseudo == "<<PERSON_1>>"


def test_hydrate_case_insensitive_lookup():
    mappings = {"<<PERSON_1>>": "Max Müller"}
    mapper = hydrate_mapper(mappings)
    pseudo = mapper.get_or_create("max müller", "PERSON")
    assert pseudo == "<<PERSON_1>>"


# -- Empty mapping --


def test_hydrate_empty():
    mapper = hydrate_mapper({})
    assert mapper.entity_count == 0
    pseudo = mapper.get_or_create("Max", "PERSON")
    assert pseudo == "<<PERSON_1>>"


# -- Roundtrip --


def test_roundtrip_consistency():
    """Mapper -> get_mapping_summary -> hydrate -> identical behavior."""
    original = PseudonymMapper()
    original.get_or_create("Max Müller", "PERSON")
    original.get_or_create("max@test.de", "EMAIL")
    original.get_or_create("Lisa Schmidt", "PERSON")

    saved = original.get_mapping_summary()
    restored = hydrate_mapper(saved)

    assert restored.reverse_lookup("<<PERSON_1>>") == "Max Müller"
    assert restored.reverse_lookup("<<PERSON_2>>") == "Lisa Schmidt"
    assert restored.reverse_lookup("<<EMAIL_1>>") == "max@test.de"

    assert restored.get_or_create("Anna Weber", "PERSON") == "<<PERSON_3>>"
    assert restored.get_or_create("anna@test.de", "EMAIL") == "<<EMAIL_2>>"


# -- Custom label hydration --


def test_hydrate_with_label_from_new_format():
    """Hydrate new-format pseudonyms with label set."""
    mappings = {
        "<<PLACEHOLDER_1>>": "Max Müller",
        "<<PLACEHOLDER_2>>": "max@test.de",
    }
    mapper = hydrate_mapper(mappings, label="PLACEHOLDER")
    assert mapper.reverse_lookup("<<PLACEHOLDER_1>>") == "Max Müller"
    assert mapper.reverse_lookup("<<PLACEHOLDER_2>>") == "max@test.de"
    pseudo = mapper.get_or_create("Lisa Schmidt", "PERSON")
    assert pseudo == "<<PLACEHOLDER_3>>"


def test_hydrate_with_label_from_old_format():
    """Old-format pseudonyms hydrated with label: global counter reconstructed."""
    mappings = {
        "<<PERSON_1>>": "Max Müller",
        "<<EMAIL_1>>": "max@test.de",
        "<<PERSON_2>>": "Lisa Schmidt",
    }
    mapper = hydrate_mapper(mappings, label="PLACEHOLDER")
    # Old pseudonyms still resolve
    assert mapper.reverse_lookup("<<PERSON_1>>") == "Max Müller"
    assert mapper.reverse_lookup("<<EMAIL_1>>") == "max@test.de"
    # New entities use the label with counter continuing from max(2, 1) = 2
    pseudo = mapper.get_or_create("Anna Weber", "PERSON")
    assert pseudo == "<<PLACEHOLDER_3>>"


def test_hydrate_with_label_empty():
    """Hydrate with label on empty mappings."""
    mapper = hydrate_mapper({}, label="PLACEHOLDER")
    pseudo = mapper.get_or_create("Max", "PERSON")
    assert pseudo == "<<PLACEHOLDER_1>>"


def test_roundtrip_with_label():
    """Mapper with label -> summary -> hydrate -> identical behavior."""
    original = PseudonymMapper(label="PLACEHOLDER")
    original.get_or_create("Max Müller", "PERSON")
    original.get_or_create("max@test.de", "EMAIL")
    original.get_or_create("Lisa Schmidt", "PERSON")

    saved = original.get_mapping_summary()
    restored = hydrate_mapper(saved, label="PLACEHOLDER")

    assert restored.reverse_lookup("<<PLACEHOLDER_1>>") == "Max Müller"
    assert restored.reverse_lookup("<<PLACEHOLDER_2>>") == "max@test.de"
    assert restored.reverse_lookup("<<PLACEHOLDER_3>>") == "Lisa Schmidt"

    assert restored.get_or_create("Anna Weber", "PERSON") == "<<PLACEHOLDER_4>>"
