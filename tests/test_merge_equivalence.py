"""Equivalence tests: the sort+sweep merge must equal the original nested loop.

`_legacy_merge_entities` below is a verbatim copy of the O(n²) implementation
that shipped through 0.1.2. It is the oracle for
`EnsembleDetector._merge_entities`: for every input the two must return exactly
the same list, in the same order, with the same entity objects.

Only the accept/replace loop was rewritten, so the oracle reuses the shipped
`_pick_winner` — the tie-break rules themselves are pinned by
`tests/test_ensemble.py`.
"""

from __future__ import annotations

import random

from noirdoc.detection.base import DetectedEntity
from noirdoc.detection.ensemble import EnsembleDetector

# --- Oracle: the pre-0.1.3 implementation ---


def _legacy_merge_entities(entities: list[DetectedEntity]) -> list[DetectedEntity]:
    sorted_ents = sorted(
        entities,
        key=lambda e: (e.start, -(e.end - e.start)),
    )

    accepted: list[DetectedEntity] = []
    for candidate in sorted_ents:
        same_type_idx = None
        for i, existing in enumerate(accepted):
            if (
                candidate.start < existing.end
                and existing.start < candidate.end
                and candidate.entity_type == existing.entity_type
            ):
                same_type_idx = i
                break

        if same_type_idx is None:
            accepted.append(candidate)
        else:
            existing = accepted[same_type_idx]
            winner = EnsembleDetector._pick_winner(existing, candidate)
            accepted[same_type_idx] = winner

    return accepted


# --- Random entity generation ---
#
# The coordinate space is deliberately small relative to the span lengths so
# that overlaps — the interesting case — dominate. Scores and sources come from
# tiny pools so score ties (and therefore the length and presidio tie-breaks)
# are hit often.

_TYPES = ("PERSON", "LOCATION", "ORGANIZATION")
_SCORES = (0.5, 0.7, 0.9)
_SOURCES = ("presidio", "gliner", "flair")


def _entity(
    rng: random.Random,
    *,
    entity_type: str,
    start: int,
    end: int,
) -> DetectedEntity:
    return DetectedEntity(
        entity_type=entity_type,
        text=f"{entity_type[0]}[{start},{end})",
        start=start,
        end=end,
        score=rng.choice(_SCORES),
        source=rng.choice(_SOURCES),
    )


def _random_entities(
    rng: random.Random,
    *,
    types: tuple[str, ...] = _TYPES,
    coord: int = 24,
    max_len: int = 8,
    zero_length_rate: float = 0.08,
    max_entities: int = 12,
) -> list[DetectedEntity]:
    entities = []
    for _ in range(rng.randrange(max_entities + 1)):
        start = rng.randrange(coord)
        length = 0 if rng.random() < zero_length_rate else rng.randrange(1, max_len + 1)
        entities.append(
            _entity(rng, entity_type=rng.choice(types), start=start, end=start + length)
        )
    return entities


def _assert_equivalent(entities: list[DetectedEntity]) -> bool:
    """Run both implementations. Returns True if the merge dropped anything."""
    expected = _legacy_merge_entities(list(entities))
    actual = EnsembleDetector([])._merge_entities(list(entities))
    assert actual == expected, f"divergence on {entities!r}: {actual!r} != {expected!r}"
    return len(expected) < len(entities)


# --- Property tests ---

_CASES = 2000


def test_sweep_matches_legacy_on_random_entity_sets():
    """Mixed types, dense overlaps, frequent score ties."""
    rng = random.Random(20260814)
    merges_seen = 0
    for _ in range(_CASES):
        if _assert_equivalent(_random_entities(rng)):
            merges_seen += 1
    # Guard against a vacuous run: most cases must actually resolve an overlap.
    assert merges_seen > _CASES // 2


def test_sweep_matches_legacy_on_single_type_chains():
    """One type only — long chains of same-type overlaps, the O(n²) worst case."""
    rng = random.Random(1312)
    merges_seen = 0
    for _ in range(_CASES):
        entities = _random_entities(rng, types=("PERSON",), coord=16, max_entities=16)
        if _assert_equivalent(entities):
            merges_seen += 1
    assert merges_seen > _CASES // 2


def test_sweep_matches_legacy_on_dual_type_overlaps():
    """Every span exists under two types, so rule 4 fires on every entity."""
    rng = random.Random(4711)
    for _ in range(_CASES):
        base = _random_entities(rng, types=("PERSON",), coord=20, max_entities=8)
        doubled = list(base) + [
            _entity(rng, entity_type="LOCATION", start=e.start, end=e.end) for e in base
        ]
        rng.shuffle(doubled)
        _assert_equivalent(doubled)


def test_sweep_matches_legacy_on_zero_length_spans():
    """Zero-length spans are frequent — they must not become sweep pointers."""
    rng = random.Random(99991)
    for _ in range(_CASES):
        entities = _random_entities(
            rng,
            types=("PERSON", "LOCATION"),
            coord=12,
            max_len=5,
            zero_length_rate=0.5,
        )
        _assert_equivalent(entities)


def test_sweep_matches_legacy_on_identical_spans():
    """Many detectors reporting the exact same span — every tie-break path."""
    rng = random.Random(777)
    for _ in range(_CASES):
        spans = [(rng.randrange(10), rng.randrange(1, 6)) for _ in range(rng.randrange(1, 4))]
        entities = [
            _entity(rng, entity_type=rng.choice(_TYPES), start=start, end=start + length)
            for start, length in spans
            for _ in range(rng.randrange(1, 4))
        ]
        rng.shuffle(entities)
        _assert_equivalent(entities)
