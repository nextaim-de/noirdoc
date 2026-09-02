"""DOCX text extraction using python-docx.

Extraction is driven by the shared surface walker in
:mod:`noirdoc.file_analysis.docx_parts` — the same walker the redaction
rewrite and the reveal use, so the surfaces the detector sees and the
surfaces the rewrite touches cannot drift apart.
"""

from __future__ import annotations

import io

from noirdoc.file_analysis.docx_parts import extract_document_texts
from noirdoc.file_analysis.extractors._zip_safety import check_ooxml_zip_safe


def extract_docx(data: bytes) -> str:
    """Extract text from a DOCX byte-string.

    Walks the document body — including content controls (``w:sdt``), tables
    nested at any depth, text boxes/shapes, and tracked changes (inserted
    text inline, deleted text as its own segment) — plus all distinct section
    headers and footers, review comments, and the footnotes/endnotes parts.
    All of these are routine PII surfaces the detector pipeline must see
    before the output is reconstructed.
    """
    from docx import Document

    check_ooxml_zip_safe(data, label="docx")
    doc = Document(io.BytesIO(data))
    return "\n".join(extract_document_texts(doc))
