from __future__ import annotations

import asyncio

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer
from presidio_analyzer.nlp_engine import NlpEngineProvider

from noirdoc.detection.base import BaseDetector, DetectedEntity

# Presidio entity labels → our canonical types
_PRESIDIO_TYPE_MAP: dict[str, str] = {
    "PERSON": "PERSON",
    "EMAIL_ADDRESS": "EMAIL",
    "PHONE_NUMBER": "PHONE",
    "IBAN_CODE": "IBAN",
    "CREDIT_CARD": "CREDIT_CARD",
    "LOCATION": "LOCATION",
    "DATE_TIME": "DATE",
    "NRP": "CUSTOM",
    "IP_ADDRESS": "IP_ADDRESS",
    "URL": "URL",
    "MEDICAL_LICENSE": "MEDICAL_LICENSE",
    "US_SSN": "MEDICAL_LICENSE",
    "ORGANIZATION": "ORGANIZATION",
    "DE_SVNR": "SVNR",
    "DE_STEUER_ID": "STEUER_ID",
}

# Entities to request from Presidio. LOCATION is now detected via FlairRecognizer
# (flair/ner-german-large) which is robust on lowercase German text, unlike spaCy.
_PRESIDIO_ENTITIES = [
    "PERSON",
    "EMAIL_ADDRESS",
    "PHONE_NUMBER",
    "IBAN_CODE",
    "CREDIT_CARD",
    "LOCATION",
    "DATE_TIME",
    "IP_ADDRESS",
    "URL",
    "MEDICAL_LICENSE",
    "US_SSN",
    "ORGANIZATION",
    "DE_SVNR",
    "DE_STEUER_ID",
]


class GermanPhoneRecognizer(PatternRecognizer):
    """Erkennt deutsche Telefonnummern in gängigen Formaten."""

    PATTERNS = [
        Pattern("DE_PHONE_1", r"\+49[\s\-]?\d{2,4}[\s\-]?\d{3,8}(?:[\s\-]\d{1,5}){0,2}", 0.7),
        Pattern("DE_PHONE_2", r"0049[\s\-]?\d{2,4}[\s\-]?\d{3,8}(?:[\s\-]\d{1,5}){0,2}", 0.7),
        Pattern("DE_PHONE_3", r"0\d{2,4}[\s/\-]\d{3,8}(?:[\s/\-]\d{1,5}){0,2}", 0.6),
        Pattern("DE_PHONE_4", r"\(0\d{2,4}\)\s?\d{3,8}(?:[\s\-]\d{1,5}){0,2}", 0.7),
    ]
    CONTEXT = ["telefon", "tel", "anrufen", "mobil", "handy", "fax", "phone", "call"]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="PHONE_NUMBER",
            supported_language="de",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )


class GermanSVNRRecognizer(PatternRecognizer):
    """Erkennt deutsche Sozialversicherungsnummern (12 Zeichen: 2d+6d+1Buchstabe+3d)."""

    PATTERNS = [
        # Spaced: "65 230785 M 014"
        Pattern("DE_SVNR_1", r"\d{2}\s\d{6}\s[A-Z]\s\d{3}", 0.6),
        # Compact: "65230785M014"
        Pattern("DE_SVNR_2", r"\d{2}\d{6}[A-Z]\d{3}", 0.4),
    ]
    CONTEXT = [
        "sozialversicherungsnummer",
        "svnr",
        "sv-nummer",
        "sv nummer",
        "versicherungsnummer",
        "rentenversicherung",
        "sozialversicherung",
        "rentenversicherungsnummer",
        "rvnr",
    ]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="DE_SVNR",
            supported_language="de",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )


class GermanSteuerIDRecognizer(PatternRecognizer):
    """Erkennt deutsche Steuerliche Identifikationsnummern (11 Ziffern, erste ≠ 0)."""

    PATTERNS = [
        # Grouped 2+3+3+3: "14 815 037 682"
        Pattern("DE_STEUERID_1", r"[1-9]\d\s\d{3}\s\d{3}\s\d{3}", 0.5),
        # Compact: "14815037682"
        Pattern("DE_STEUERID_2", r"[1-9]\d{10}", 0.3),
    ]
    CONTEXT = [
        "steuer-id",
        "steuerid",
        "steueridentifikationsnummer",
        "steuerliche identifikationsnummer",
        "tin",
        "identifikationsnummer",
        "idnr",
        "steuernummer",
        "finanzamt",
    ]

    def __init__(self) -> None:
        super().__init__(
            supported_entity="DE_STEUER_ID",
            supported_language="de",
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )


class InvertedNameRecognizer(PatternRecognizer):
    """Detects names in 'Lastname, Firstname' format (common in spreadsheets/databases)."""

    PATTERNS = [
        # Lastname, Firstname (handles umlauts, hyphens)
        Pattern("INV_NAME_1", r"[A-ZÄÖÜ][a-zäöüß\-]+,\s+[A-ZÄÖÜ][a-zäöüß]+", 0.85),
        # Lastname, Title Firstname (Dr., Prof.)
        Pattern(
            "INV_NAME_2",
            r"[A-ZÄÖÜ][a-zäöüß\-]+,\s+(?:Dr\.|Prof\.)\s+[A-ZÄÖÜ][a-zäöüß]+",
            0.90,
        ),
        # Hyphenated-Lastname, Firstname
        Pattern(
            "INV_NAME_3",
            r"[A-ZÄÖÜ][a-zäöüß]+\-[A-ZÄÖÜ][a-zäöüß]+,\s+[A-ZÄÖÜ][a-zäöüß]+",
            0.90,
        ),
    ]
    CONTEXT = ["name", "nachname", "vorname", "person", "mitarbeiter", "patient", "kunde"]

    def __init__(self, supported_language: str = "de") -> None:
        super().__init__(
            supported_entity="PERSON",
            supported_language=supported_language,
            patterns=self.PATTERNS,
            context=self.CONTEXT,
        )


class PresidioDetector(BaseDetector):
    """
    Wrapper um Presidio AnalyzerEngine.
    Nutzt SpaCy NLP Engine mit deutschen und englischen Modellen.
    """

    def __init__(self, languages: list[str] | None = None) -> None:
        if languages is None:
            languages = ["de", "en"]
        self._languages = languages
        self._analyzer = self._build_analyzer(languages)

    def _build_analyzer(self, languages: list[str]) -> AnalyzerEngine:
        model_map = {
            "de": "de_core_news_lg",
            "en": "en_core_web_lg",
        }

        models = [
            {"lang_code": lang, "model_name": model_map[lang]}
            for lang in languages
            if lang in model_map
        ]

        nlp_config = {
            "nlp_engine_name": "spacy",
            "models": models,
        }

        nlp_engine = NlpEngineProvider(nlp_configuration=nlp_config).create_engine()

        analyzer = AnalyzerEngine(
            nlp_engine=nlp_engine,
            supported_languages=languages,
        )

        if "de" in languages:
            analyzer.registry.add_recognizer(GermanPhoneRecognizer())
            analyzer.registry.add_recognizer(GermanSVNRRecognizer())
            analyzer.registry.add_recognizer(GermanSteuerIDRecognizer())
            analyzer.registry.add_recognizer(InvertedNameRecognizer(supported_language="de"))
        if "en" in languages:
            analyzer.registry.add_recognizer(InvertedNameRecognizer(supported_language="en"))

        return analyzer

    async def detect(self, text: str, language: str = "de") -> list[DetectedEntity]:
        if not text:
            return []

        results = await asyncio.to_thread(
            self._analyzer.analyze,
            text=text,
            language=language,
            entities=_PRESIDIO_ENTITIES,
        )

        entities: list[DetectedEntity] = []
        for r in results:
            entity_type = _PRESIDIO_TYPE_MAP.get(r.entity_type, "CUSTOM")
            entities.append(
                DetectedEntity(
                    entity_type=entity_type,
                    text=text[r.start : r.end],
                    start=r.start,
                    end=r.end,
                    score=round(r.score, 4),
                    source="presidio",
                ),
            )

        return entities

    @property
    def name(self) -> str:
        return "presidio"
