from __future__ import annotations

import asyncio

from gliner import GLiNER

from noirdoc.detection.base import BaseDetector, DetectedEntity

# GLiNER natürlichsprachige Labels → kanonische Entity-Types
# Mapping für knowledgator/gliner-pii-edge-v1.0
_LABEL_MAP: dict[str, str] = {
    # Person
    "name": "PERSON",
    "first name": "PERSON",
    "last name": "PERSON",
    # Location (granulare Labels → LOCATION)
    "location address": "LOCATION",
    "location street": "LOCATION",
    "location city": "LOCATION",
    "location state": "LOCATION",
    "location country": "LOCATION",
    "location zip": "LOCATION",
    # Contact
    "email address": "EMAIL",
    "phone number": "PHONE",
    "ip address": "IP_ADDRESS",
    "url": "URL",
    # Financial
    "credit card": "CREDIT_CARD",
    "iban": "IBAN",
    # IDs
    "ssn": "SVNR",
    # Dates
    "dob": "DATE",
    "date": "DATE",
    # Orgs
    "organization": "ORGANIZATION",
    "company name": "ORGANIZATION",
    "business name": "ORGANIZATION",
}

_LABELS = list(_LABEL_MAP.keys())


class GlinerDetector(BaseDetector):
    """
    Wrapper um GLiNER für kontextuelle NER.
    Nutzt ein vortrainiertes Multilingual-Modell.
    """

    def __init__(self, model_name: str = "knowledgator/gliner-pii-edge-v1.0") -> None:
        try:
            self._model = GLiNER.from_pretrained(model_name, local_files_only=True)
        except OSError:
            self._model = GLiNER.from_pretrained(model_name)

    async def detect(self, text: str, language: str = "de") -> list[DetectedEntity]:
        if not text:
            return []

        raw = await asyncio.to_thread(
            self._model.predict_entities,
            text,
            _LABELS,
            threshold=0.65,
        )

        entities: list[DetectedEntity] = []
        for item in raw:
            label = item["label"].lower()
            entity_type = _LABEL_MAP.get(label)
            if entity_type is None:
                continue
            entities.append(
                DetectedEntity(
                    entity_type=entity_type,
                    text=item["text"],
                    start=item["start"],
                    end=item["end"],
                    score=round(item["score"], 4),
                    source="gliner",
                ),
            )

        return entities

    @property
    def name(self) -> str:
        return "gliner"
