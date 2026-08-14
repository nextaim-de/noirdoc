"""Equivalence tests: the single-pass builder vs the old back-to-front splice.

`_legacy_pseudonymize` below is a verbatim copy of the O(entities × len(text))
replacement loop that shipped through 0.1.2. It is the oracle for
`PseudonymizationEngine.pseudonymize` on NON-OVERLAPPING entities: same output
bytes, same mapping, same pseudonym numbering.

Overlapping entities are the one deliberate difference — the old loop spliced
pseudonyms into already-substituted text and corrupted the output. That change
is pinned by
`tests/test_pseudonymization.py::test_pseudonymize_char_overlapping_remainder_is_masked`,
and the overlapping shapes get their own property test at the bottom of this
file: no oracle to compare against, so the invariants are round-trip identity
and full masking.
"""

from __future__ import annotations

import random
import string

from noirdoc.detection.base import DetectedEntity
from noirdoc.pseudonymization.engine import PseudonymizationEngine
from noirdoc.pseudonymization.mapper import PseudonymMapper
from noirdoc.reidentification.engine import ReidentificationEngine

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


# --- Overlapping entities: no oracle, invariants instead ---
#
# The old loop is not a usable oracle here — it corrupted overlapping output.
# Two invariants stand in for it:
#
#   1. reidentify(pseudonymize(text)) == text, and
#   2. nothing a detector flagged reaches the output in cleartext.
#
# (2) is only a sound substring check if every span has a distinct text, so
# the generator draws its NON-whitespace characters from an alphabet of unique
# characters, each occurring at most once — any slice holding one of them
# occurs exactly once in the text. That alphabet is deliberately disjoint from
# the pseudonym charset (`<`, `>`, `_`, uppercase, digits) so a one-character
# span cannot "appear" inside a placeholder, and it has no uppercase, so the
# mapper's `strip().lower()` key cannot collapse two different spans onto one
# pseudonym. Case collisions are a real mapper property and are pinned
# explicitly by
# `test_pseudonymization.py::test_pseudonymize_char_case_variants_share_one_mapping`;
# keeping them out here is what makes the invariants above decidable.
#
# Whitespace IS sprinkled in, because remainders cut at arbitrary offsets and
# the trimming that keeps their mappings clean has to hold under random
# shapes. Entity edges are snapped off whitespace, the way real detector spans
# are.

_UNIQUE_ALPHABET = string.ascii_lowercase + "!#$%&()*+,-./:;=?@[]^{|}~"
_WHITESPACE = " \t\n"


def _random_text(rng: random.Random) -> str:
    out: list[str] = []
    for char in _UNIQUE_ALPHABET[: rng.randrange(20, len(_UNIQUE_ALPHABET) + 1)]:
        out.append(char)
        if rng.random() < 0.25:
            out.append(rng.choice(_WHITESPACE))
    return "".join(out)


def _random_overlapping_case(rng: random.Random) -> tuple[str, list[DetectedEntity]]:
    text = _random_text(rng)
    entities: list[DetectedEntity] = []
    for _ in range(rng.randrange(1, 9)):
        start = rng.randrange(len(text))
        end = min(len(text), start + rng.randrange(1, 12))
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start == end:
            continue
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
    rng.shuffle(entities)
    return text, entities


def _overhangs(text: str, entities: list[DetectedEntity]) -> list[str]:
    """The slices a clip would have dropped: loser characters past the winner."""
    slices: list[str] = []
    consumed_to = 0
    for entity in sorted(entities, key=lambda e: e.start):
        if entity.start < consumed_to < entity.end:
            slices.append(text[consumed_to : entity.end])
        consumed_to = max(consumed_to, entity.end)
    return slices


def test_overlapping_entities_round_trip_and_leave_no_cleartext():
    """Every flagged character is masked, and the reveal restores the original."""
    rng = random.Random(20260814)
    reident = ReidentificationEngine()
    overhangs_seen = 0
    trimmed_overhangs_seen = 0

    for _ in range(_CASES):
        text, entities = _random_overlapping_case(rng)

        mapper = PseudonymMapper()
        pseudonymized = PseudonymizationEngine().pseudonymize(text, list(entities), mapper)

        assert reident.reidentify(pseudonymized, mapper) == text, (
            f"round-trip broke on {text!r} / {entities!r}"
        )

        for entity in entities:
            assert entity.text not in pseudonymized, (
                f"cleartext {entity.text!r} survived in {pseudonymized!r}"
            )

        for overhang in _overhangs(text, entities):
            overhangs_seen += 1
            core = overhang.strip()
            if core != overhang:
                trimmed_overhangs_seen += 1
            # Whitespace is emitted as literal text, the core never is.
            if core:
                assert core not in pseudonymized

    # Guard against a vacuous run: the shapes this test exists for must occur,
    # including remainders that need trimming.
    assert overhangs_seen > _CASES // 2
    assert trimmed_overhangs_seen > _CASES // 10
