from __future__ import annotations

import pytest

pytest.importorskip("gliner")

from noirdoc.detection.gliner_detector import GlinerDetector  # noqa: E402

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def detector() -> GlinerDetector:
    return GlinerDetector(model_name="knowledgator/gliner-pii-edge-v1.0")


def _types(entities):
    return {e.entity_type for e in entities}


# --- Kontextuelles NER ---


async def test_german_person(detector):
    entities = await detector.detect(
        "Der Geschäftsführer Hans-Peter Schmidt hat die Firma verlassen.",
        "de",
    )
    assert "PERSON" in _types(entities)


async def test_german_org_and_location(detector):
    entities = await detector.detect(
        "Sie arbeitet bei der Deutschen Telekom in Bonn.",
        "de",
    )
    types = _types(entities)
    assert "ORGANIZATION" in types or "LOCATION" in types


async def test_german_complex_name(detector):
    entities = await detector.detect(
        "Frau Dr. Elisabeth von Hohenstein-Berger",
        "de",
    )
    assert "PERSON" in _types(entities)


async def test_german_person_and_location_context(detector):
    entities = await detector.detect(
        "Kontakt: Herr Meier aus Darmstadt",
        "de",
    )
    types = _types(entities)
    assert "PERSON" in types


# --- Edge Cases ---


async def test_empty_string(detector):
    entities = await detector.detect("", "de")
    assert entities == []


async def test_no_entities(detector):
    entities = await detector.detect("Keine Entitäten.", "de")
    pii_types = {"PERSON", "EMAIL", "PHONE", "IBAN", "CREDIT_CARD"}
    found_types = _types(entities)
    assert not found_types.intersection(pii_types)


async def test_entity_has_correct_fields(detector):
    entities = await detector.detect(
        "Max Müller wohnt in Berlin.",
        "de",
    )
    person = next((e for e in entities if e.entity_type == "PERSON"), None)
    if person:
        assert person.source == "gliner"
        assert 0.0 <= person.score <= 1.0
        assert person.start >= 0
        assert person.end > person.start


async def test_name_property(detector):
    assert detector.name == "gliner"
