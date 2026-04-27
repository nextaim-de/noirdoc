from __future__ import annotations

from noirdoc.detection.base import BaseDetector, DetectedEntity
from noirdoc.detection.ensemble import EnsembleDetector

# --- Helpers ---


class FakeDetector(BaseDetector):
    def __init__(self, results: list[DetectedEntity], detector_name: str = "fake") -> None:
        self._results = results
        self._name = detector_name

    async def detect(self, text: str, language: str = "de") -> list[DetectedEntity]:
        return self._results

    @property
    def name(self) -> str:
        return self._name


class FailingDetector(BaseDetector):
    async def detect(self, text: str, language: str = "de") -> list[DetectedEntity]:
        raise RuntimeError("boom")

    @property
    def name(self) -> str:
        return "failing"


def _ent(
    entity_type: str,
    text: str,
    start: int,
    end: int,
    score: float,
    source: str,
) -> DetectedEntity:
    return DetectedEntity(
        entity_type=entity_type,
        text=text,
        start=start,
        end=end,
        score=score,
        source=source,
    )


# --- Tests ---


async def test_overlap_higher_score_wins():
    """Both detectors find same entity, GLiNER has higher score."""
    ent_a = _ent("PERSON", "Max Müller", 0, 10, 0.85, "presidio")
    ent_b = _ent("PERSON", "Max Müller", 0, 10, 0.92, "gliner")
    ensemble = EnsembleDetector(
        [FakeDetector([ent_a]), FakeDetector([ent_b])],
        score_threshold=0.0,
    )
    result = await ensemble.detect("Max Müller", "de")
    assert len(result) == 1
    assert result[0].score == 0.92
    assert result[0].source == "gliner"


async def test_no_overlap_both_kept():
    """Non-overlapping entities from different detectors are both kept."""
    ent_a = _ent("PERSON", "Max", 0, 3, 0.85, "presidio")
    ent_b = _ent("PHONE", "0171-123", 20, 28, 0.75, "gliner")
    ensemble = EnsembleDetector(
        [FakeDetector([ent_a]), FakeDetector([ent_b])],
        score_threshold=0.0,
    )
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 2


async def test_partial_overlap_longer_span_wins():
    """Overlapping entities: longer span with higher score wins."""
    ent_a = _ent("PERSON", "Max Müller", 0, 10, 0.85, "presidio")
    ent_b = _ent("PERSON", "Max", 0, 3, 0.70, "gliner")
    ensemble = EnsembleDetector(
        [FakeDetector([ent_a]), FakeDetector([ent_b])],
        score_threshold=0.0,
    )
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 1
    assert result[0].text == "Max Müller"


async def test_score_threshold_filters():
    """Entities below threshold are removed."""
    ent = _ent("PERSON", "vielleicht", 0, 10, 0.3, "presidio")
    ensemble = EnsembleDetector(
        [FakeDetector([ent])],
        score_threshold=0.5,
    )
    result = await ensemble.detect("dummy", "de")
    assert result == []


async def test_single_detector():
    """Works with a single detector, no merge needed."""
    ent = _ent("EMAIL", "a@b.com", 5, 12, 0.95, "presidio")
    ensemble = EnsembleDetector([FakeDetector([ent])], score_threshold=0.0)
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 1
    assert result[0].entity_type == "EMAIL"


async def test_empty_text():
    """Empty text returns no entities."""
    ensemble = EnsembleDetector(
        [FakeDetector([_ent("PERSON", "X", 0, 1, 0.9, "presidio")])],
    )
    result = await ensemble.detect("", "de")
    assert result == []


async def test_results_sorted_by_start():
    """Results are sorted by start position."""
    ent_a = _ent("EMAIL", "a@b.com", 30, 37, 0.9, "presidio")
    ent_b = _ent("PERSON", "Max", 0, 3, 0.9, "gliner")
    ensemble = EnsembleDetector(
        [FakeDetector([ent_a]), FakeDetector([ent_b])],
        score_threshold=0.0,
    )
    result = await ensemble.detect("dummy", "de")
    assert result[0].start < result[1].start


async def test_tie_score_longer_span_wins():
    """Same score: longer span wins."""
    ent_a = _ent("PERSON", "Max", 0, 3, 0.85, "presidio")
    ent_b = _ent("PERSON", "Max Müller", 0, 10, 0.85, "gliner")
    ensemble = EnsembleDetector(
        [FakeDetector([ent_a]), FakeDetector([ent_b])],
        score_threshold=0.0,
    )
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 1
    assert result[0].text == "Max Müller"


async def test_tie_score_tie_length_presidio_wins():
    """Same score, same length: presidio wins as tiebreaker."""
    ent_a = _ent("PERSON", "Max Müller", 0, 10, 0.85, "gliner")
    ent_b = _ent("PERSON", "Max Müller", 0, 10, 0.85, "presidio")
    ensemble = EnsembleDetector(
        [FakeDetector([ent_a]), FakeDetector([ent_b])],
        score_threshold=0.0,
    )
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 1
    assert result[0].source == "presidio"


async def test_failing_detector_does_not_crash():
    """A failing detector is gracefully ignored."""
    ent = _ent("PERSON", "Max", 0, 3, 0.9, "presidio")
    ensemble = EnsembleDetector(
        [FailingDetector(), FakeDetector([ent])],
        score_threshold=0.0,
    )
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 1


async def test_failing_detector_logs_warning(capsys):
    """A failing detector must log a warning so operators can detect degraded state."""
    ent = _ent("PERSON", "Max", 0, 3, 0.9, "presidio")
    ensemble = EnsembleDetector(
        [FailingDetector(), FakeDetector([ent])],
        score_threshold=0.0,
    )
    await ensemble.detect("dummy", "de")
    captured = capsys.readouterr().out + capsys.readouterr().err
    assert "detection.detector_failed" in captured


# --- PERSON validation ---


async def test_person_with_strong_reject_word_filtered():
    """PERSON entities containing non-name words like 'überwiesen' are rejected."""
    ent = _ent("PERSON", "Euro, überwiesen", 0, 17, 0.85, "presidio")
    ensemble = EnsembleDetector([FakeDetector([ent])], score_threshold=0.0)
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 0


async def test_person_trailing_und_filtered():
    """PERSON entities ending with 'und' (boundary absorption) are rejected."""
    ent = _ent("PERSON", "Hoffmann, und", 0, 13, 0.85, "presidio")
    ensemble = EnsembleDetector([FakeDetector([ent])], score_threshold=0.0)
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 0


async def test_person_von_in_name_kept():
    """'von' inside a multi-word name is NOT rejected."""
    ent = _ent("PERSON", "Max von Müller", 0, 14, 0.85, "presidio")
    ensemble = EnsembleDetector([FakeDetector([ent])], score_threshold=0.0)
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 1


async def test_single_word_person_kept():
    """Single-word PERSON entities are never rejected."""
    ent = _ent("PERSON", "Lena", 0, 4, 0.85, "presidio")
    ensemble = EnsembleDetector([FakeDetector([ent])], score_threshold=0.0)
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 1


async def test_non_person_entity_with_reject_word_unaffected():
    """Non-PERSON entities are never filtered by person validation."""
    ent = _ent("ORGANIZATION", "Euro GmbH", 0, 9, 0.85, "presidio")
    ensemble = EnsembleDetector([FakeDetector([ent])], score_threshold=0.0)
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 1


async def test_person_wohnhaft_filtered():
    """PERSON entity containing 'wohnhaft' is rejected."""
    ent = _ent("PERSON", "Regensburg, wohnhaft", 0, 20, 0.7, "presidio")
    ensemble = EnsembleDetector([FakeDetector([ent])], score_threshold=0.0)
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 0


# --- Dual-type overlap ---


async def test_overlap_different_types_both_kept():
    """Overlapping entities with different types are both kept."""
    ent_a = _ent("PERSON", "Hoffmann", 0, 8, 0.85, "presidio")
    ent_b = _ent("ORGANIZATION", "Hoffmann", 0, 8, 0.80, "gliner")
    ensemble = EnsembleDetector(
        [FakeDetector([ent_a]), FakeDetector([ent_b])],
        score_threshold=0.0,
    )
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 2
    types = {e.entity_type for e in result}
    assert types == {"PERSON", "ORGANIZATION"}


async def test_overlap_same_type_still_picks_winner():
    """Overlapping entities with same type still pick a winner (no duplication)."""
    ent_a = _ent("PERSON", "Hoffmann", 0, 8, 0.85, "presidio")
    ent_b = _ent("PERSON", "Hoffmann", 0, 8, 0.90, "gliner")
    ensemble = EnsembleDetector(
        [FakeDetector([ent_a]), FakeDetector([ent_b])],
        score_threshold=0.0,
    )
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 1
    assert result[0].score == 0.90


# --- DATE threshold ---


async def test_date_threshold_filters_low_confidence():
    """DATE entities below 0.7 are filtered by per-type threshold."""
    ent = _ent("DATE", "2024", 0, 4, 0.6, "presidio")
    ensemble = EnsembleDetector([FakeDetector([ent])], score_threshold=0.5)
    result = await ensemble.detect("dummy", "de")
    assert result == []


async def test_date_threshold_keeps_high_confidence():
    """DATE entities at or above 0.7 are kept."""
    ent = _ent("DATE", "15. März 2024", 0, 14, 0.8, "presidio")
    ensemble = EnsembleDetector([FakeDetector([ent])], score_threshold=0.5)
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 1


# --- Three-detector ensemble ---


async def test_three_detectors_parallel():
    """Three detectors (presidio, flair, gliner) all contribute entities."""
    ent_presidio = _ent("EMAIL", "a@b.com", 0, 7, 0.95, "presidio")
    ent_flair = _ent("PERSON", "Max Müller", 20, 30, 0.88, "flair")
    ent_gliner = _ent("LOCATION", "Berlin", 40, 46, 0.82, "gliner")
    ensemble = EnsembleDetector(
        [
            FakeDetector([ent_presidio], "presidio"),
            FakeDetector([ent_flair], "flair"),
            FakeDetector([ent_gliner], "gliner"),
        ],
        score_threshold=0.0,
    )
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 3
    sources = {e.source for e in result}
    assert sources == {"presidio", "flair", "gliner"}


async def test_flair_and_presidio_same_span_same_type_picks_winner():
    """Flair and presidio on same span with same type: higher score wins."""
    ent_presidio = _ent("PERSON", "Hoffmann", 0, 8, 0.80, "presidio")
    ent_flair = _ent("PERSON", "Hoffmann", 0, 8, 0.92, "flair")
    ensemble = EnsembleDetector(
        [FakeDetector([ent_presidio]), FakeDetector([ent_flair])],
        score_threshold=0.0,
    )
    result = await ensemble.detect("dummy", "de")
    assert len(result) == 1
    assert result[0].source == "flair"
    assert result[0].score == 0.92
