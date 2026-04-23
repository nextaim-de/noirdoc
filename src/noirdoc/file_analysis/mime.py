"""MIME type detection and base64 data-URI utilities."""

from __future__ import annotations

import base64
import re

# MIME types that LLM providers accept as inline content blocks.
# Anything NOT in this set must be converted to text before forwarding.
PROVIDER_PASSABLE_MIMES: set[str] = {
    "application/pdf",
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/tiff",
    "text/plain",
    "text/csv",
    "text/markdown",
    "text/html",
}

# Maps MIME types to internal format identifiers used by extractors.
MIME_TO_FORMAT: dict[str, str] = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/webp": "image",
    "image/tiff": "image",
    "text/plain": "plain",
    "text/csv": "plain",
    "text/markdown": "plain",
    "text/html": "plain",
}

_DATA_URI_RE = re.compile(r"^data:([^;,]+)")


def detect_mime_from_data_uri(data_uri: str) -> str | None:
    """Extract the MIME type from a ``data:`` URI."""
    match = _DATA_URI_RE.match(data_uri)
    return match.group(1) if match else None


def decode_base64_data_uri(data_uri: str) -> tuple[bytes, str]:
    """Decode a ``data:`` URI into ``(raw_bytes, mime_type)``.

    Raises ``ValueError`` when the URI cannot be parsed.
    """
    match = _DATA_URI_RE.match(data_uri)
    if not match:
        raise ValueError(f"Invalid data URI: {data_uri[:80]}")
    mime_type = match.group(1)

    # Strip the header portion: "data:<mime>;base64,"
    try:
        _, b64_part = data_uri.split(",", 1)
    except ValueError:
        raise ValueError("data URI missing comma separator")

    raw = base64.b64decode(b64_part)
    return raw, mime_type


def format_for_mime(mime_type: str) -> str | None:
    """Return the internal format identifier for *mime_type*, or ``None`` if unsupported."""
    return MIME_TO_FORMAT.get(mime_type)
