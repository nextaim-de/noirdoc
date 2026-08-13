"""Equivalence tests: the single-pass builder vs the old back-to-front splice.

`_legacy_pseudonymize` below is a verbatim copy of the O(entities × len(text))
replacement loop that shipped through 0.1.2. It is the oracle for
`PseudonymizationEngine.pseudonymize` on NON-OVERLAPPING entities: same output
bytes, same mapping, same pseudonym numbering.

Overlapping entities are the one deliberate difference — the old loop spliced
pseudonyms into already-substituted text and corrupted the output. That change
is pinned by
`tests/test_pseudonymization.py::test_pseudonymize_char_overlapping_entities_are_clipped`.
"""

from __future__ import annotations

import random

from noirdoc.detection.base import DetectedEntity
from noirdoc.pseudonymization.engine import PseudonymizationEngine
from noirdoc.pseudonymization.mapper import PseudonymMapper

# --- Oracle: the pre-0.1.3 implementation ---


def _legacy_pseudonymize(
    text: str,
    entities: list[DetectedEntity],
    mapper: PseudonymMapper,
) -> str:
    sorted_entities = sorted(entities, key=lambda e: e.start, reverse=True)
    result = text
    for entity in sorted_entities:
        pseudonym = mapper.get_or_create(entity.text, entity.entity_type)
        result = result[: entity.start] + pseudonym + result[entity.end :]
    return result


# --- Random non-overlapping entity generation ---
#
# A four-character alphabet makes entity texts repeat often, so the mapper's
# "same text -> same pseudonym" reuse is exercised alongside the offsets.

_ALPHABET = "ab c"
_TYPES = ("PERSON", "LOCATION", "EMAIL")


def _random_case(rng: random.Random) -> tuple[str, list[DetectedEntity]]:
    text = "".join(rng.choice(_ALPHABET) for _ in range(rng.randrange(60)))
    entities: list[DetectedEntity] = []
    cursor = 0
    while True:
        start = cursor + rng.randrange(3)
        end = start + rng.randrange(1, 6)
        if end > len(text):
            break
        entities.append(
            DetectedEntity(
                entity_type=rng.choice(_TYPES),
                text=text[start:end],
                start=start,
                end=end,
                score=0.9,
                source="test",
            )
        )
        cursor = end
    rng.shuffle(entities)
    return text, entities


def _mapper_state(mapper: PseudonymMapper) -> tuple[dict[str, str], dict[str, object]]:
    return mapper.get_mapping_summary(), mapper.get_counts_summary()


# --- Property tests ---

_CASES = 2000


def test_single_pass_matches_legacy_on_non_overlapping_entities():
    """Byte-identical output and identical mapper state, whatever the input order."""
    rng = random.Random(20260814)
    entities_seen = 0
    for _ in range(_CASES):
        text, entities = _random_case(rng)
        entities_seen += len(entities)

        legacy_mapper = PseudonymMapper()
        expected = _legacy_pseudonymize(text, list(entities), legacy_mapper)

        new_mapper = PseudonymMapper()
        actual = PseudonymizationEngine().pseudonymize(text, list(entities), new_mapper)

        assert actual == expected, f"divergence on {text!r} / {entities!r}"
        assert _mapper_state(new_mapper) == _mapper_state(legacy_mapper)

    # Guard against a vacuous run.
    assert entities_seen > _CASES * 5


def test_single_pass_matches_legacy_with_a_labelled_mapper():
    """A single-label mapper collapses every type onto one counter — same result."""
    rng = random.Random(31337)
    for _ in range(_CASES // 4):
        text, entities = _random_case(rng)

        legacy_mapper = PseudonymMapper(label="PII")
        expected = _legacy_pseudonymize(text, list(entities), legacy_mapper)

        new_mapper = PseudonymMapper(label="PII")
        actual = PseudonymizationEngine().pseudonymize(text, list(entities), new_mapper)

        assert actual == expected
        assert _mapper_state(new_mapper) == _mapper_state(legacy_mapper)
