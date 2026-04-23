from __future__ import annotations

from noirdoc.detection.base import DetectedEntity
from noirdoc.pseudonymization.mapper import PseudonymMapper


class PseudonymizationEngine:
    """Ersetzt erkannte Entities im Text durch Pseudonyme."""

    def pseudonymize(
        self,
        text: str,
        entities: list[DetectedEntity],
        mapper: PseudonymMapper,
    ) -> str:
        """
        Entities von hinten nach vorne ersetzen, damit Offsets stimmen.
        """
        sorted_entities = sorted(entities, key=lambda e: e.start, reverse=True)
        result = text
        for entity in sorted_entities:
            pseudonym = mapper.get_or_create(entity.text, entity.entity_type)
            result = result[: entity.start] + pseudonym + result[entity.end :]
        return result
