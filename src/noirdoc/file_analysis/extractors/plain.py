"""Plain-text extraction (TXT, CSV, MD, HTML)."""

from __future__ import annotations


def extract_plain(data: bytes) -> str:
    """Decode raw bytes as UTF-8 text."""
    return data.decode("utf-8", errors="replace")
