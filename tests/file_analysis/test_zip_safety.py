"""NDS-015: refuse OOXML inputs whose zip envelope looks like a bomb."""

from __future__ import annotations

import io
import zipfile

import pytest

from noirdoc.file_analysis.extractors._zip_safety import check_ooxml_zip_safe


def _build_zip(entries: list[tuple[str, bytes]], *, compress: bool = True) -> bytes:
    """Build a zip archive with the given (name, payload) entries."""
    buf = io.BytesIO()
    method = zipfile.ZIP_DEFLATED if compress else zipfile.ZIP_STORED
    with zipfile.ZipFile(buf, mode="w", compression=method) as zf:
        for name, payload in entries:
            zf.writestr(name, payload)
    return buf.getvalue()


def test_check_ooxml_passes_for_normal_archive():
    payload = _build_zip([("word/document.xml", b"<doc>hello</doc>")])
    check_ooxml_zip_safe(payload, label="docx")


def test_check_ooxml_rejects_non_zip_input():
    with pytest.raises(ValueError, match="not a valid zip archive"):
        check_ooxml_zip_safe(b"not a zip", label="docx")


def test_check_ooxml_rejects_oversized_uncompressed():
    """A single highly-compressible 300 MB blob must be refused."""
    bomb = b"A" * (300 * 1024 * 1024)
    payload = _build_zip([("payload.bin", bomb)])
    with pytest.raises(ValueError, match="bytes uncompressed"):
        check_ooxml_zip_safe(payload, label="xlsx")


def test_check_ooxml_rejects_extreme_compression_ratio():
    """A 10 MB blob of zeros compresses to a tiny file — ratio guard catches it."""
    bomb = b"\x00" * (10 * 1024 * 1024)
    payload = _build_zip([("payload.bin", bomb)])
    with pytest.raises(ValueError, match="compression ratio"):
        check_ooxml_zip_safe(payload, label="docx")
