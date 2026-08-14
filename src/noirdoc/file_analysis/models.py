"""Data models for the file analysis pipeline."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from noirdoc.detection.base import DetectedEntity
from noirdoc.pseudonymization.engine import EmittedSpan


class FileAnalysisMode(enum.StrEnum):
    PASSTHROUGH = "passthrough"
    DETECT_ONLY = "detect_only"
    BLOCK = "block"
    PSEUDONYMIZE = "pseudonymize"


@dataclass
class FileBlock:
    """A single file found in an API request body."""

    content_bytes: bytes
    mime_type: str
    source_path: str  # JSON path, e.g. "messages[0].content[1]"
    source_type: str  # Provider block type: "file", "image_url", "document", etc.
    extracted_text: str | None = None
    entities: list[DetectedEntity] = field(default_factory=list)
    pseudonymized_text: str | None = None
    # What the pseudonymizer actually emitted: original -> placeholder, in
    # output order. Reconstruction needs it to rewrite DOCX runs and XLSX
    # cells; None means "not pseudonymized here", and reconstruction refuses
    # rather than guessing the pairing from entity offsets.
    emitted_spans: list[EmittedSpan] | None = None
    reconstructed_bytes: bytes | None = None
    extraction_error: str | None = None


@dataclass
class FileAnalysisResult:
    """Aggregated results from analysing all files in a request."""

    files_analyzed: int = 0
    files_converted: int = 0  # Files converted to text blocks (PDF/images)
    files_reconstructed: int = 0  # Files reconstructed in-place (DOCX/XLSX/plain)
    files_blocked: int = 0
    files_extraction_errors: int = 0
    total_entities: int = 0
    entity_types: dict[str, int] = field(default_factory=dict)
    blocked: bool = False  # True if request should be rejected (block mode)
    blocks: list[FileBlock] = field(default_factory=list)
