"""Top-level file text extractor that dispatches to format-specific extractors."""

from __future__ import annotations

import asyncio

import structlog

from noirdoc.file_analysis.mime import format_for_mime
from noirdoc.file_analysis.models import FileBlock

logger = structlog.get_logger()


class FileTextExtractor:
    """Dispatch to the correct format extractor for each :class:`FileBlock`."""

    def __init__(self, *, ocr_enabled: bool = False, max_pages: int = 50) -> None:
        self._ocr_enabled = ocr_enabled
        self._max_pages = max_pages

    async def extract_text(self, block: FileBlock) -> str | None:
        """Extract text from *block*. Returns ``None`` on failure."""
        fmt = format_for_mime(block.mime_type)
        if fmt is None:
            block.extraction_error = f"Unsupported MIME type: {block.mime_type}"
            return None

        try:
            if fmt == "pdf":
                return await asyncio.to_thread(self._extract_pdf, block.content_bytes)
            if fmt == "docx":
                return await asyncio.to_thread(self._extract_docx, block.content_bytes)
            if fmt == "xlsx":
                return await asyncio.to_thread(self._extract_xlsx, block.content_bytes)
            if fmt == "image":
                if not self._ocr_enabled:
                    block.extraction_error = "OCR disabled for this tenant"
                    return None
                return await asyncio.to_thread(self._extract_ocr, block.content_bytes)
            if fmt == "plain":
                from noirdoc.file_analysis.extractors.plain import extract_plain

                return extract_plain(block.content_bytes)
        except Exception as exc:
            block.extraction_error = str(exc)
            logger.warning(
                "file_analysis.extraction_failed",
                mime=block.mime_type,
                error=str(exc),
            )
            return None

        return None  # pragma: no cover – unreachable when MIME_TO_FORMAT is correct

    # -- sync helpers run via to_thread -----------------------------------------

    def _extract_pdf(self, data: bytes) -> str:
        if self._ocr_enabled:
            from noirdoc.file_analysis.extractors.pdf import extract_pdf_with_ocr_fallback

            return extract_pdf_with_ocr_fallback(data, max_pages=self._max_pages)

        from noirdoc.file_analysis.extractors.pdf import extract_pdf

        return extract_pdf(data, max_pages=self._max_pages)

    def _extract_docx(self, data: bytes) -> str:
        from noirdoc.file_analysis.extractors.docx_ext import extract_docx

        return extract_docx(data)

    def _extract_xlsx(self, data: bytes) -> str:
        from noirdoc.file_analysis.extractors.xlsx import extract_xlsx

        return extract_xlsx(data)

    def _extract_ocr(self, data: bytes) -> str:
        from noirdoc.file_analysis.extractors.ocr import extract_ocr

        return extract_ocr(data)
