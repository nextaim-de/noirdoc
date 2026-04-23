from __future__ import annotations

import asyncio

from noirdoc.detection.base import BaseDetector, DetectedEntity

_TAG_MAP: dict[str, str] = {
    "PER": "PERSON",
    "LOC": "LOCATION",
    "ORG": "ORGANIZATION",
}


class FlairDetector(BaseDetector):
    """Eigenständiger Flair-basierter NER-Detektor für deutsche Texte.

    Nutzt flair/ner-german-large (XLM-R, F1=92.3% auf CoNLL-03 DE).
    Robuster bei Lowercase-Text als spaCy.
    """

    def __init__(self, model_name: str = "flair/ner-german-large") -> None:
        from flair.models import SequenceTagger

        self._model = SequenceTagger.load(model_name)

    async def detect(self, text: str, language: str = "de") -> list[DetectedEntity]:
        if not text or language != "de":
            return []

        return await asyncio.to_thread(self._predict, text)

    def _predict(self, text: str) -> list[DetectedEntity]:
        from flair.data import Sentence

        sentence = Sentence(text)
        self._model.predict(sentence)

        entities: list[DetectedEntity] = []
        for span in sentence.get_spans("ner"):
            entity_type = _TAG_MAP.get(span.tag)
            if entity_type is None:
                continue
            entities.append(
                DetectedEntity(
                    entity_type=entity_type,
                    text=span.text,
                    start=span.start_position,
                    end=span.end_position,
                    score=round(span.score, 2),
                    source="flair",
                ),
            )
        return entities

    @property
    def name(self) -> str:
        return "flair"
