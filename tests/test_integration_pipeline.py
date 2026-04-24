from __future__ import annotations

import pytest

pytest.importorskip("gliner")

from noirdoc.detection.ensemble import EnsembleDetector  # noqa: E402
from noirdoc.detection.gliner_detector import GlinerDetector  # noqa: E402
from noirdoc.detection.presidio_detector import PresidioDetector  # noqa: E402
from noirdoc.pseudonymization.engine import PseudonymizationEngine  # noqa: E402
from noirdoc.pseudonymization.mapper import PseudonymMapper  # noqa: E402
from noirdoc.reidentification.engine import ReidentificationEngine  # noqa: E402

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def detector() -> EnsembleDetector:
    presidio = PresidioDetector(languages=["de", "en"])
    gliner = GlinerDetector(model_name="knowledgator/gliner-pii-edge-v1.0")
    return EnsembleDetector(detectors=[presidio, gliner])


async def test_german_roundtrip(detector):
    text = (
        "Max Müller (max.mueller@example.com) wohnt in der Hauptstr. 42, "
        "10115 Berlin. IBAN: DE89 3704 0044 0532 0130 00"
    )
    entities = await detector.detect(text, "de")
    assert len(entities) >= 3

    mapper = PseudonymMapper()
    pseudonymized = PseudonymizationEngine().pseudonymize(text, entities, mapper)
    assert "Max Müller" not in pseudonymized
    assert "max.mueller@example.com" not in pseudonymized
    assert "<<PERSON_1>>" in pseudonymized or mapper.entity_count > 0

    reidentified = ReidentificationEngine().reidentify(pseudonymized, mapper)
    assert reidentified == text


async def test_english_roundtrip(detector):
    text = "John Smith works at Google in Mountain View. Email: john@google.com"
    entities = await detector.detect(text, "en")

    mapper = PseudonymMapper()
    pseudonymized = PseudonymizationEngine().pseudonymize(text, entities, mapper)
    reidentified = ReidentificationEngine().reidentify(pseudonymized, mapper)
    assert reidentified == text


async def test_no_pii(detector):
    text = "Das Wetter ist heute schön."
    entities = await detector.detect(text, "de")

    mapper = PseudonymMapper()
    pseudonymized = PseudonymizationEngine().pseudonymize(text, entities, mapper)
    assert pseudonymized == text


async def test_location_detection(detector):
    """LOCATION should be detected via FlairRecognizer."""
    text = "Max Mustermann wohnt in Berlin und arbeitet in München."
    entities = await detector.detect(text, "de")
    types = {e.entity_type for e in entities}
    assert "LOCATION" in types, f"LOCATION not found. Got: {types}"


async def test_lowercase_german_financial_text_no_false_positives(detector):
    """Lowercase German financial/legal terms must not be detected as PII."""
    text = (
        "sind enthaltene währunggewinne bei aktiengewinnen nach §8b kstg "
        "bei der ermittlung des zu versteuernden einkommens ebenfalls "
        "nach §8 b begünstigt oder nicht?"
    )
    entities = await detector.detect(text, "de")
    assert entities == [], f"Unexpected entities: {[(e.text, e.entity_type) for e in entities]}"


async def test_consistent_pseudonyms(detector):
    text = "Max Müller trifft Max Müller."
    entities = await detector.detect(text, "de")

    mapper = PseudonymMapper()
    pseudonymized = PseudonymizationEngine().pseudonymize(text, entities, mapper)

    # Both occurrences of "Max Müller" should use the same pseudonym
    person_pseudos = [p for p in mapper.get_all_pseudonyms() if p.startswith("<<PERSON_")]
    if person_pseudos:
        assert pseudonymized.count(person_pseudos[0]) == 2
