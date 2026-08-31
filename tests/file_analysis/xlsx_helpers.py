"""Shared helpers for XLSX tests: a deterministic detector and workbook builders.

Everything here is in-code — the repo keeps no binary fixtures.
"""

from __future__ import annotations

import html
import io
import zipfile

from openpyxl import Workbook

from noirdoc.detection.base import DetectedEntity


class SubstringDetector:
    """Offset-correct fake detector: every occurrence of a known needle is an entity.

    ``FakeDetector`` in ``tests/test_ensemble.py`` returns fixed results regardless
    of input, which is useless for anything that replaces text by offset.
    """

    def __init__(self, table: dict[str, str]) -> None:
        self._table = table

    async def detect(self, text: str, language: str = "de") -> list[DetectedEntity]:
        out: list[DetectedEntity] = []
        for needle, entity_type in self._table.items():
            start = text.find(needle)
            while start != -1:
                out.append(
                    DetectedEntity(
                        entity_type=entity_type,
                        text=needle,
                        start=start,
                        end=start + len(needle),
                        score=0.9,
                        source="fake",
                    )
                )
                start = text.find(needle, start + 1)
        return out


def workbook_bytes(wb: Workbook) -> bytes:
    """Serialize an openpyxl workbook to bytes."""
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def part_names(xlsx: bytes) -> list[str]:
    with zipfile.ZipFile(io.BytesIO(xlsx)) as zf:
        return zf.namelist()


def read_part(xlsx: bytes, name: str) -> bytes:
    with zipfile.ZipFile(io.BytesIO(xlsx)) as zf:
        return zf.read(name)


def parts_containing(xlsx: bytes, needle: str) -> list[str]:
    """Names of every zip part whose decoded content contains *needle* (raw or XML-escaped)."""
    forms = {needle, html.escape(needle, quote=False)}
    hits: list[str] = []
    with zipfile.ZipFile(io.BytesIO(xlsx)) as zf:
        for name in zf.namelist():
            text = zf.read(name).decode("utf-8", errors="ignore")
            if any(form in text for form in forms):
                hits.append(name)
    return hits


def assert_no_part_contains(xlsx: bytes, needles: list[str]) -> None:
    """The leger ``verify_redaction.py`` gate as a test helper: grep EVERY part."""
    leaks = {n: parts_containing(xlsx, n) for n in needles}
    leaks = {n: parts for n, parts in leaks.items() if parts}
    assert not leaks, f"original strings survived in output parts: {leaks}"
