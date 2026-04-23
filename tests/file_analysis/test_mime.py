"""Tests for MIME type detection and base64 data-URI utilities."""

from __future__ import annotations

import base64

import pytest

from noirdoc.file_analysis.mime import (
    decode_base64_data_uri,
    detect_mime_from_data_uri,
    format_for_mime,
)


class TestDetectMimeFromDataUri:
    def test_pdf(self):
        assert detect_mime_from_data_uri("data:application/pdf;base64,abc") == "application/pdf"

    def test_png(self):
        assert detect_mime_from_data_uri("data:image/png;base64,abc") == "image/png"

    def test_no_match(self):
        assert detect_mime_from_data_uri("not-a-data-uri") is None

    def test_empty(self):
        assert detect_mime_from_data_uri("") is None


class TestDecodeBase64DataUri:
    def test_simple(self):
        payload = base64.b64encode(b"hello").decode()
        raw, mime = decode_base64_data_uri(f"data:text/plain;base64,{payload}")
        assert raw == b"hello"
        assert mime == "text/plain"

    def test_invalid_uri(self):
        with pytest.raises(ValueError):
            decode_base64_data_uri("nope")

    def test_missing_comma(self):
        with pytest.raises(ValueError):
            decode_base64_data_uri("data:text/plain;base64")


class TestFormatForMime:
    @pytest.mark.parametrize(
        "mime,expected",
        [
            ("application/pdf", "pdf"),
            ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
            ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "xlsx"),
            ("image/png", "image"),
            ("image/jpeg", "image"),
            ("text/plain", "plain"),
            ("text/csv", "plain"),
            ("text/markdown", "plain"),
        ],
    )
    def test_known_types(self, mime, expected):
        assert format_for_mime(mime) == expected

    def test_unknown(self):
        assert format_for_mime("application/x-custom") is None
