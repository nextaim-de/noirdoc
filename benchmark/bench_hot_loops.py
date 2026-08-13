"""Ad-hoc micro-benchmark for the two masking hot loops.

Not wired into CI. Run it by hand when touching `_merge_entities` or
`PseudonymizationEngine.pseudonymize`::

    uv run python benchmark/bench_hot_loops.py

It times the shipped implementations against the pre-0.1.3 ones, which are
copied in below — the same code that serves as the oracle in
``tests/test_merge_equivalence.py`` and ``tests/test_pseudonymize_equivalence.py``,
where correctness (rather than speed) is pinned.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from noirdoc.detection.base import DetectedEntity
from noirdoc.detection.ensemble import EnsembleDetector
from noirdoc.pseudonymization.engine import PseudonymizationEngine
from noirdoc.pseudonymization.mapper import PseudonymMapper

# --- Pre-0.1.3 implementations ---


def legacy_merge_entities(entities: list[DetectedEntity]) -> list[DetectedEntity]:
    sorted_ents = sorted(entities, key=lambda e: (e.start, -(e.end - e.start)))

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
            accepted[same_type_idx] = EnsembleDetector._pick_winner(existing, candidate)

    return accepted


def legacy_pseudonymize(
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


# --- Synthetic input ---

_SPAN = 8
_STRIDE = 12


def make_entities(count: int) -> list[DetectedEntity]:
    """`count` entities laid out like a real ensemble result.

    Every third span is reported twice — once by presidio, once by gliner —
    so the merge actually resolves overlaps instead of only appending.
    """
    entities: list[DetectedEntity] = []
    for i in range(count):
        start = (i // 3 * 2 + i % 3) * _STRIDE if i % 3 else i * _STRIDE
        entities.append(
            DetectedEntity(
                entity_type="PERSON" if i % 2 else "LOCATION",
                text=f"entity-{i}",
                start=start,
                end=start + _SPAN,
                score=0.9 if i % 3 else 0.8,
                source="presidio" if i % 2 else "gliner",
            )
        )
    return entities


def make_text(count: int, min_length: int = 0) -> str:
    return "x" * max(count * _STRIDE + _SPAN, min_length)


def make_disjoint_entities(count: int) -> list[DetectedEntity]:
    """`count` non-overlapping entities — the pseudonymize input shape."""
    return [
        DetectedEntity(
            entity_type="PERSON",
            text=f"entity-{i}",
            start=i * _STRIDE,
            end=i * _STRIDE + _SPAN,
            score=0.9,
            source="presidio",
        )
        for i in range(count)
    ]


# --- Timing ---


def best_of(fn: Callable[[], object], *, rounds: int = 5, inner: int) -> float:
    """Best per-call wall time in milliseconds."""
    best = float("inf")
    for _ in range(rounds):
        start = time.perf_counter()
        for _ in range(inner):
            fn()
        best = min(best, (time.perf_counter() - start) / inner)
    return best * 1000


def merge_cases(
    entities: list[DetectedEntity],
) -> tuple[Callable[[], object], Callable[[], object]]:
    detector = EnsembleDetector([])
    return (
        lambda: legacy_merge_entities(entities),
        lambda: detector._merge_entities(entities),
    )


def pseudonymize_cases(
    text: str,
    entities: list[DetectedEntity],
) -> tuple[Callable[[], object], Callable[[], object]]:
    engine = PseudonymizationEngine()
    return (
        lambda: legacy_pseudonymize(text, entities, PseudonymMapper()),
        lambda: engine.pseudonymize(text, entities, PseudonymMapper()),
    )


def report(name: str, count: int, chars: str, old_ms: float, new_ms: float) -> None:
    print(
        f"| {name} | {count:>4} | {chars:>7} | {old_ms:9.4f} | {new_ms:9.4f} "
        f"| {old_ms / new_ms:6.1f}x |"
    )


def main() -> None:
    print("| loop | entities | text | old (ms) | new (ms) | speedup |")
    print("| --- | ---: | ---: | ---: | ---: | ---: |")

    for count in (10, 100, 1000):
        old, new = merge_cases(make_entities(count))
        inner = max(1, 2000 // count)
        report(
            "_merge_entities",
            count,
            "—",
            best_of(old, inner=inner),
            best_of(new, inner=inner),
        )

    # The old substitution copied the whole text once per entity, so its cost
    # is entities × text length: the last row is the shape that hurts.
    for count, min_chars in ((10, 0), (100, 0), (1000, 0), (1000, 200_000)):
        text = make_text(count, min_chars)
        old, new = pseudonymize_cases(text, make_disjoint_entities(count))
        inner = max(1, 2000 // count)
        report(
            "pseudonymize",
            count,
            f"{len(text):,}",
            best_of(old, inner=inner),
            best_of(new, inner=inner),
        )


if __name__ == "__main__":
    main()
