"""NDS-016: DOCX extraction must cover header/footer/comment surfaces."""

from __future__ import annotations

import io

from noirdoc.detection.base import DetectedEntity
from noirdoc.file_analysis.extractors.docx_ext import extract_docx
from noirdoc.file_analysis.models import FileBlock
from noirdoc.file_analysis.reconstruction import _reconstruct_docx


def _entity(text: str, in_text: str) -> DetectedEntity:
    start = in_text.index(text)
    return DetectedEntity(
        entity_type="PERSON",
        text=text,
        start=start,
        end=start + len(text),
        score=0.9,
        source="test",
    )


def _docx_with_headers_footers_and_body() -> bytes:
    """Build a DOCX whose header, footer, and body each contain distinct PII."""
    from docx import Document

    doc = Document()
    section = doc.sections[0]
    section.header.paragraphs[0].text = "Header: Anna Mueller"
    section.footer.paragraphs[0].text = "Footer: Bernd Schmidt"
    doc.add_paragraph("Body: Carla Weber")

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def test_extract_docx_walks_headers_and_footers():
    """PII embedded in section headers and footers must reach the detector."""
    text = extract_docx(_docx_with_headers_footers_and_body())
    assert "Anna Mueller" in text
    assert "Bernd Schmidt" in text
    assert "Carla Weber" in text


def test_extract_docx_walks_comments():
    """Review comments are a routine PII surface — they must be extracted."""
    from docx import Document

    doc = Document()
    para = doc.add_paragraph("Body text")
    doc.add_comment(runs=[para.runs[0]] if para.runs else [], text="Reviewer: Dora Klein")
    buf = io.BytesIO()
    doc.save(buf)

    text = extract_docx(buf.getvalue())
    assert "Dora Klein" in text


def test_reconstruct_docx_replaces_text_in_headers_and_footers():
    """Reconstruction must scrub header/footer text so the output bytes are clean."""
    docx_bytes = _docx_with_headers_footers_and_body()
    extracted = extract_docx(docx_bytes)

    # Build a synthetic pseudonymized result: replace each name with a token
    pseudonymized = (
        extracted.replace("Anna Mueller", "<<PERSON_1>>")
        .replace("Bernd Schmidt", "<<PERSON_2>>")
        .replace("Carla Weber", "<<PERSON_3>>")
    )

    entities = [
        _entity("Anna Mueller", extracted),
        _entity("Bernd Schmidt", extracted),
        _entity("Carla Weber", extracted),
    ]

    block = FileBlock(
        content_bytes=docx_bytes,
        mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        source_path="test.docx",
        source_type="file",
        extracted_text=extracted,
        pseudonymized_text=pseudonymized,
        entities=entities,
    )

    new_bytes = _reconstruct_docx(block)
    assert new_bytes is not None

    rewritten = extract_docx(new_bytes)
    assert "Anna Mueller" not in rewritten
    assert "Bernd Schmidt" not in rewritten
    assert "Carla Weber" not in rewritten
    assert "<<PERSON_1>>" in rewritten
    assert "<<PERSON_2>>" in rewritten
    assert "<<PERSON_3>>" in rewritten
