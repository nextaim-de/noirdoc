from __future__ import annotations

import pytest

from noirdoc.detection.flair_recognizer import FlairRecognizer

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def recognizer() -> FlairRecognizer:
    return FlairRecognizer()


def test_detects_person(recognizer):
    results = recognizer.analyze("Max Mustermann wohnt in Berlin.", entities=["PERSON"])
    assert any(r.entity_type == "PERSON" for r in results)


def test_detects_location(recognizer):
    results = recognizer.analyze("Max Mustermann wohnt in Berlin.", entities=["LOCATION"])
    assert any(r.entity_type == "LOCATION" for r in results)


def test_detects_organization(recognizer):
    results = recognizer.analyze(
        "Die Deutsche Telekom hat ihren Sitz in Bonn.",
        entities=["ORGANIZATION"],
    )
    assert any(r.entity_type == "ORGANIZATION" for r in results)


def test_lowercase_location(recognizer):
    """Flair should detect locations even in lowercase text — unlike spaCy."""
    results = recognizer.analyze(
        "er wohnt in berlin und arbeitet in münchen.",
        entities=["LOCATION"],
    )
    location_texts = [r.entity_type for r in results if r.entity_type == "LOCATION"]
    assert len(location_texts) >= 1


def test_empty_string(recognizer):
    results = recognizer.analyze("", entities=["PERSON", "LOCATION"])
    assert results == []


def test_no_entities(recognizer):
    results = recognizer.analyze(
        "Das Wetter ist schön.",
        entities=["PERSON", "LOCATION", "ORGANIZATION"],
    )
    assert results == []
