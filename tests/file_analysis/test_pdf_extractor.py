"""Tests for PDF text extraction and OCR fallback.

The OCR fallback covers DocuSign-style PDFs whose body is rastered (scans,
flattened XFA, permission-restricted text) while only a short stamp (e.g.
envelope ID) is in the real text layer. See the `diagnose_pdf.py` script
for how we identified the class of problem.
"""

from __future__ import annotations

import io
import shutil

import pytest

from noirdoc.file_analysis.extractor import FileTextExtractor
from noirdoc.file_analysis.extractors.pdf import (
    extract_pdf,
    extract_pdf_with_ocr_fallback,
)
from noirdoc.file_analysis.models import FileBlock

requires_tesseract = pytest.mark.skipif(
    shutil.which("tesseract") is None,
    reason="tesseract binary not available",
)


_TEXT_PDF_BYTES = b"""%PDF-1.4
1 0 obj
<</Type/Catalog/Pages 2 0 R>>
endobj
2 0 obj
<</Type/Pages/Kids[3 0 R]/Count 1>>
endobj
3 0 obj
<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>
endobj
4 0 obj
<</Length 160>>
stream
BT /F1 18 Tf 50 750 Td (Invoice 2024 Acme Corp 1234 Main Street Berlin) Tj
0 -30 Td (Total amount 4567 EUR payable within 30 days to IBAN DE89 3704 0044 0532 0130 00) Tj ET
endstream
endobj
5 0 obj
<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>
endobj
xref
0 6
0000000000 65535 f
0000000009 00000 n
0000000054 00000 n
0000000095 00000 n
0000000198 00000 n
0000000408 00000 n
trailer
<</Size 6/Root 1 0 R>>
startxref
465
%%EOF
"""


def _make_image_pdf(text: str) -> bytes:
    """A single-page PDF whose content is a raster image — no real text layer.

    Mimics the DocuSign-from-scan case: OCR on rendered pages recovers the
    body; pypdfium2.get_text_range() returns essentially nothing.
    """
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (1200, 1600), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 48)
    except OSError:
        font = ImageFont.load_default()
    draw.text((60, 100), text, fill="black", font=font)

    buf = io.BytesIO()
    img.save(buf, format="PDF", resolution=150.0)
    return buf.getvalue()


@pytest.fixture
def text_pdf_bytes() -> bytes:
    return _TEXT_PDF_BYTES


@pytest.fixture
def image_pdf_bytes() -> bytes:
    return _make_image_pdf("Contract between Alice and Bob for 500 EUR")


# ── extract_pdf (baseline, unchanged) ────────────────────────────────────────


def test_extract_pdf_returns_text_layer(text_pdf_bytes):
    text = extract_pdf(text_pdf_bytes)
    assert "Invoice 2024 Acme Corp" in text
    assert "DE89 3704 0044 0532 0130 00" in text


def test_extract_pdf_returns_empty_for_image_only_pdf(image_pdf_bytes):
    text = extract_pdf(image_pdf_bytes)
    assert text.strip() == ""


# ── extract_pdf_with_ocr_fallback (new) ──────────────────────────────────────


def test_fallback_keeps_text_layer_when_above_threshold(text_pdf_bytes):
    text = extract_pdf_with_ocr_fallback(text_pdf_bytes, min_chars_per_page=50)
    assert "Invoice 2024 Acme Corp" in text
    assert "DE89 3704 0044 0532 0130 00" in text


@requires_tesseract
def test_fallback_runs_ocr_when_text_layer_is_sparse(image_pdf_bytes):
    text = extract_pdf_with_ocr_fallback(
        image_pdf_bytes,
        min_chars_per_page=50,
        ocr_lang="eng",
    )
    lower = text.lower()
    assert "contract" in lower
    assert "alice" in lower
    assert "bob" in lower


# ── FileTextExtractor dispatcher ─────────────────────────────────────────────


async def test_dispatcher_uses_text_only_path_when_ocr_disabled(image_pdf_bytes):
    extractor = FileTextExtractor(ocr_enabled=False)
    block = FileBlock(
        content_bytes=image_pdf_bytes,
        mime_type="application/pdf",
        source_path="scan.pdf",
        source_type="file",
    )
    result = await extractor.extract_text(block)
    assert result is not None
    assert result.strip() == ""


@requires_tesseract
async def test_dispatcher_falls_back_to_ocr_when_ocr_enabled(image_pdf_bytes):
    extractor = FileTextExtractor(ocr_enabled=True)
    block = FileBlock(
        content_bytes=image_pdf_bytes,
        mime_type="application/pdf",
        source_path="scan.pdf",
        source_type="file",
    )
    result = await extractor.extract_text(block)
    assert result is not None
    lower = result.lower()
    assert "contract" in lower
    assert "alice" in lower


async def test_dispatcher_uses_text_path_for_text_based_pdf_even_with_ocr(text_pdf_bytes):
    extractor = FileTextExtractor(ocr_enabled=True)
    block = FileBlock(
        content_bytes=text_pdf_bytes,
        mime_type="application/pdf",
        source_path="invoice.pdf",
        source_type="file",
    )
    result = await extractor.extract_text(block)
    assert result is not None
    assert "Invoice 2024 Acme Corp" in result
