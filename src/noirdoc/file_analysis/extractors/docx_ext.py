"""DOCX text extraction using python-docx."""

from __future__ import annotations

import io


def extract_docx(data: bytes) -> str:
    """Extract text from a DOCX byte-string (paragraphs + table cells)."""
    from docx import Document

    doc = Document(io.BytesIO(data))
    parts: list[str] = []

    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            parts.append(text)

    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                text = cell.text.strip()
                if text:
                    parts.append(text)

    return "\n".join(parts)
