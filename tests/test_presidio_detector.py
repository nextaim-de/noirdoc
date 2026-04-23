from __future__ import annotations

import pytest

from noirdoc.detection.presidio_detector import PresidioDetector

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def detector() -> PresidioDetector:
    return PresidioDetector(languages=["de", "en"])


def _types(entities):
    return {e.entity_type for e in entities}


# --- German ---


async def test_german_person(detector):
    entities = await detector.detect("Max Müller wohnt in Berlin.", "de")
    types = _types(entities)
    assert "PERSON" in types


async def test_presidio_does_not_detect_location(detector):
    """LOCATION is excluded from Presidio — GLiNER handles it instead."""
    entities = await detector.detect("Max Müller wohnt in Berlin.", "de")
    assert "LOCATION" not in _types(entities)


async def test_german_iban(detector):
    entities = await detector.detect("Meine IBAN ist DE89 3704 0044 0532 0130 00.", "de")
    assert "IBAN" in _types(entities)


async def test_german_iban_no_spaces(detector):
    entities = await detector.detect("IBAN: DE89370400440532013000", "de")
    assert "IBAN" in _types(entities)


async def test_german_phone(detector):
    entities = await detector.detect("Erreichbar unter +49 171 1234567.", "de")
    assert "PHONE" in _types(entities)


async def test_german_phone_three_groups(detector):
    """Phone numbers with 3 digit groups after country code must be fully captured."""
    entities = await detector.detect(
        "Seine Mobilnummer ist +49 176 8834 2219.",
        "de",
    )
    phones = [e for e in entities if e.entity_type == "PHONE"]
    assert len(phones) >= 1
    assert "2219" in phones[0].text


async def test_german_svnr(detector):
    """Detect Sozialversicherungsnummer in context."""
    entities = await detector.detect(
        "Sozialversicherungsnummer lautet 65 230785 M 014",
        "de",
    )
    assert "SVNR" in _types(entities)


async def test_german_svnr_compact(detector):
    """Detect compact SVNR format in context."""
    entities = await detector.detect(
        "Die SVNR des Versicherten ist 65230785M014.",
        "de",
    )
    assert "SVNR" in _types(entities)


async def test_german_steuer_id(detector):
    """Detect Steuer-ID in context."""
    entities = await detector.detect("Steuer-ID ist 14 815 037 682", "de")
    assert "STEUER_ID" in _types(entities)


async def test_german_steuer_id_compact(detector):
    """Detect compact Steuer-ID in context."""
    entities = await detector.detect(
        "Steueridentifikationsnummer 14815037682",
        "de",
    )
    assert "STEUER_ID" in _types(entities)


async def test_german_email(detector):
    entities = await detector.detect("Mail an max.mueller@example.com", "de")
    assert "EMAIL" in _types(entities)


async def test_credit_card(detector):
    entities = await detector.detect("Credit card: 4111 1111 1111 1111", "en")
    assert "CREDIT_CARD" in _types(entities)


async def test_german_ip_address(detector):
    entities = await detector.detect("Server-IP: 192.168.1.1", "de")
    assert "IP_ADDRESS" in _types(entities)


# --- English ---


async def test_english_person(detector):
    entities = await detector.detect("John Smith lives in New York.", "en")
    types = _types(entities)
    assert "PERSON" in types


async def test_english_email(detector):
    entities = await detector.detect("His email is john@example.com", "en")
    assert "EMAIL" in _types(entities)


# --- Edge Cases ---


async def test_empty_string(detector):
    entities = await detector.detect("", "de")
    assert entities == []


async def test_no_pii(detector):
    entities = await detector.detect("Keine PII hier.", "de")
    assert entities == []


async def test_lowercase_german_financial_terms_not_detected(detector):
    """Lowercase German compound nouns must not be flagged as PII."""
    text = (
        "sind enthaltene währunggewinne bei aktiengewinnen nach §8b kstg "
        "bei der ermittlung des zu versteuernden einkommens ebenfalls "
        "nach §8 b begünstigt oder nicht?"
    )
    entities = await detector.detect(text, "de")
    detected_texts = {e.text for e in entities}
    assert "währunggewinne" not in detected_texts
    assert "aktiengewinnen" not in detected_texts


async def test_entity_has_correct_fields(detector):
    entities = await detector.detect("Mail an max.mueller@example.com", "de")
    email = next(e for e in entities if e.entity_type == "EMAIL")
    assert email.text == "max.mueller@example.com"
    assert email.source == "presidio"
    assert 0.0 <= email.score <= 1.0
    assert email.start >= 0
    assert email.end > email.start


async def test_name_property(detector):
    assert detector.name == "presidio"
