from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel

ENTITY_TYPES = {
    "PERSON",
    "EMAIL",
    "PHONE",
    "IBAN",
    "CREDIT_CARD",
    "LOCATION",
    "DATE",
    "ORGANIZATION",
    "IP_ADDRESS",
    "URL",
    "MEDICAL_LICENSE",
    "SVNR",
    "STEUER_ID",
    "CUSTOM",
}


class DetectedEntity(BaseModel):
    """Eine erkannte PII-Entität mit Position im Text."""

    entity_type: str
    text: str
    start: int
    end: int
    score: float
    source: str


class BaseDetector(ABC):
    """Interface das jeder Detektor implementieren muss."""

    @abstractmethod
    async def detect(self, text: str, language: str = "de") -> list[DetectedEntity]: ...

    @property
    @abstractmethod
    def name(self) -> str: ...
