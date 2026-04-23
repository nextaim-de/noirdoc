"""Mode-specific decision logic for file analysis."""

from __future__ import annotations

from noirdoc.file_analysis.models import FileAnalysisMode


class FileAnalysisPolicy:
    """Determine which pipeline steps to run based on the analysis mode."""

    def __init__(self, mode: FileAnalysisMode) -> None:
        self.mode = mode

    def should_extract_text(self) -> bool:
        return self.mode != FileAnalysisMode.PASSTHROUGH

    def should_detect_pii(self) -> bool:
        return self.mode in (
            FileAnalysisMode.DETECT_ONLY,
            FileAnalysisMode.BLOCK,
            FileAnalysisMode.PSEUDONYMIZE,
        )

    def should_pseudonymize(self) -> bool:
        return self.mode == FileAnalysisMode.PSEUDONYMIZE

    def should_block_on_pii(self) -> bool:
        return self.mode == FileAnalysisMode.BLOCK
