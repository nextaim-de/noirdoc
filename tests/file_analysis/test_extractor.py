"""Tests for the FileTextExtractor dispatcher and plain-text extraction."""

from __future__ import annotations

import pytest

from noirdoc.file_analysis.extractor import FileTextExtractor
from noirdoc.file_analysis.models import FileBlock


@pytest.fixture
def extractor():
    return FileTextExtractor(ocr_enabled=False)


async def test_plain_text_extraction(extractor):
    block = FileBlock(
        content_bytes=b"Hello World",
        mime_type="text/plain",
        source_path="test",
        source_type="file",
    )
    result = await extractor.extract_text(block)
    assert result == "Hello World"


async def test_csv_extraction(extractor):
    block = FileBlock(
        content_bytes=b"name,email\nJohn,john@test.com",
        mime_type="text/csv",
        source_path="test",
        source_type="file",
    )
    result = await extractor.extract_text(block)
    assert "name,email" in result
    assert "John,john@test.com" in result


async def test_unsupported_mime_type(extractor):
    block = FileBlock(
        content_bytes=b"binary",
        mime_type="application/x-unknown",
        source_path="test",
        source_type="file",
    )
    result = await extractor.extract_text(block)
    assert result is None
    assert block.extraction_error is not None
    assert "Unsupported MIME type" in block.extraction_error


async def test_ocr_disabled_for_images(extractor):
    block = FileBlock(
        content_bytes=b"fake-image",
        mime_type="image/png",
        source_path="test",
        source_type="image",
    )
    result = await extractor.extract_text(block)
    assert result is None
    assert "OCR disabled" in (block.extraction_error or "")


async def test_corrupt_data_sets_error(extractor):
    block = FileBlock(
        content_bytes=b"not-a-real-pdf",
        mime_type="application/pdf",
        source_path="test",
        source_type="file",
    )
    result = await extractor.extract_text(block)
    assert result is None
    assert block.extraction_error is not None


async def test_ocr_decompression_bomb_refused(monkeypatch):
    """A PNG that decodes to >50 MP must be refused, not OOM the process."""
    import io

    from PIL import Image

    # Force the bomb threshold low so the test stays fast; the production
    # constant is 50 MP.
    monkeypatch.setattr(
        "noirdoc.file_analysis.extractors.ocr._MAX_IMAGE_PIXELS",
        100,
    )
    huge = Image.new("RGB", (50, 50), color="white")
    buf = io.BytesIO()
    huge.save(buf, format="PNG")
    block = FileBlock(
        content_bytes=buf.getvalue(),
        mime_type="image/png",
        source_path="test",
        source_type="image",
    )
    result = await FileTextExtractor(ocr_enabled=True).extract_text(block)
    assert result is None
    assert "decompression bomb" in (block.extraction_error or "").lower()
