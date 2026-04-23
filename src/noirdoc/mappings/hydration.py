from __future__ import annotations

import re

from noirdoc.pseudonymization.mapper import PseudonymMapper

PSEUDO_PATTERN = re.compile(r"^<<([A-Z_]+)_(\d+)>>$")


def hydrate_mapper(
    mappings: dict[str, str],
    prefix: str = "<<",
    suffix: str = ">>",
    label: str | None = None,
) -> PseudonymMapper:
    """
    Reconstruct a PseudonymMapper from a stored mapping dict.

    Input:  {"<<PERSON_1>>": "Max Müller", "<<EMAIL_1>>": "max@test.de"}
    Output: PseudonymMapper with correct internal state and counters.

    When ``label`` is None (default), counters are reconstructed per entity
    type so that new entities continue with the next number (e.g. PERSON_3
    after PERSON_1 and PERSON_2).

    When ``label`` is set (e.g. "PLACEHOLDER"), a single global counter is
    reconstructed by taking the maximum counter value across all stored
    pseudonyms, regardless of their original type labels.
    """
    mapper = PseudonymMapper(prefix=prefix, suffix=suffix, label=label)

    for pseudonym, original in mappings.items():
        lookup_key = original.strip().lower()
        mapper._entity_to_pseudo[lookup_key] = pseudonym
        mapper._pseudo_to_entity[pseudonym] = original

        match = PSEUDO_PATTERN.match(pseudonym)
        if match:
            counter = int(match.group(2))
            if label is not None:
                current_max = mapper._counters.get(label, 0)
                mapper._counters[label] = max(current_max, counter)
            else:
                entity_type = match.group(1)
                current_max = mapper._counters.get(entity_type, 0)
                mapper._counters[entity_type] = max(current_max, counter)

    return mapper
