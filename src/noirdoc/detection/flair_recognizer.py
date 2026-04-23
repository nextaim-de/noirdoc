from __future__ import annotations

from presidio_analyzer import EntityRecognizer, RecognizerResult


class FlairRecognizer(EntityRecognizer):
    """Flair-basierter NER-Recognizer für deutsche Texte.

    Nutzt flair/ner-german-large (XLM-R, F1=92.3% auf CoNLL-03 DE).
    Robuster bei Lowercase-Text als spaCy.
    """

    PRESIDIO_EQUIVALENCES = {
        "PER": "PERSON",
        "LOC": "LOCATION",
        "ORG": "ORGANIZATION",
    }

    def __init__(self, model_name: str = "flair/ner-german-large") -> None:
        # Set attributes BEFORE super().__init__ because it calls self.load()
        self._model_name = model_name
        self._model = None
        super().__init__(
            supported_entities=list(self.PRESIDIO_EQUIVALENCES.values()),
            supported_language="de",
            name="Flair NER",
        )

    def _ensure_model(self):
        if self._model is None:
            from flair.models import SequenceTagger

            self._model = SequenceTagger.load(self._model_name)

    def load(self) -> None:
        self._ensure_model()

    def analyze(
        self,
        text: str,
        entities: list[str] | None = None,
        nlp_artifacts=None,
    ) -> list[RecognizerResult]:
        if not text:
            return []

        self._ensure_model()

        from flair.data import Sentence

        sentence = Sentence(text)
        self._model.predict(sentence)  # type: ignore[attr-defined]

        results: list[RecognizerResult] = []
        for span in sentence.get_spans("ner"):
            entity_type = self.PRESIDIO_EQUIVALENCES.get(span.tag)
            if entity_type is None:
                continue
            if entities and entity_type not in entities:
                continue
            results.append(
                RecognizerResult(
                    entity_type=entity_type,
                    start=span.start_position,
                    end=span.end_position,
                    score=round(span.score, 2),
                ),
            )
        return results
