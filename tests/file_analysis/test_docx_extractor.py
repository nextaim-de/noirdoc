"""NDS-016: DOCX extraction must cover header/footer/comment surfaces.

Issue #17: extraction and rewrite must also cover content controls, nested
tables, footnotes/endnotes, text boxes, tracked changes — and the rewrite
must not drop inline pictures.
"""

from __future__ import annotations

import io

from noirdoc.detection.base import DetectedEntity
from noirdoc.file_analysis.extractors.docx_ext import extract_docx
from noirdoc.file_analysis.models import FileBlock
from noirdoc.file_analysis.reconstruction import _reconstruct_docx
from tests.file_analysis.docx_helpers import (
    TINY_PNG,
    add_block_sdt,
    add_endnotes_part,
    add_footnotes_part,
    add_nested_table,
    add_textbox_paragraph,
    add_tracked_changes_paragraph,
    all_xml,
    docx_bytes,
)

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


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


def _redact(data: bytes, names: list[str]) -> bytes:
    """Redact *names* from DOCX *data* the way the pipeline does.

    Extracts, builds a synthetic pseudonymized text (``<<PERSON_N>>`` per
    name) plus matching entities, and runs ``_reconstruct_docx``.
    """
    extracted = extract_docx(data)
    pseudonymized = extracted
    entities = []
    for i, name in enumerate(names, start=1):
        if name in extracted:
            entities.append(_entity(name, extracted))
            pseudonymized = pseudonymized.replace(name, f"<<PERSON_{i}>>")

    block = FileBlock(
        content_bytes=data,
        mime_type=DOCX_MIME,
        source_path="test.docx",
        source_type="file",
        extracted_text=extracted,
        pseudonymized_text=pseudonymized,
        entities=entities,
    )
    new_bytes = _reconstruct_docx(block)
    assert new_bytes is not None
    return new_bytes


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


# ── Issue #17 blind spots ────────────────────────────────────────────────


def test_extract_docx_walks_content_controls():
    """Text inside w:sdt (form fields, "Bearbeiter:" boxes) must be extracted."""
    from docx import Document

    doc = Document()
    add_block_sdt(doc, "Bearbeiter: Anna Mueller")

    text = extract_docx(docx_bytes(doc))
    assert "Anna Mueller" in text


def test_extract_docx_walks_nested_tables():
    """A table inside a table cell must be extracted at any depth."""
    from docx import Document

    doc = Document()
    add_nested_table(doc, outer_text="Outer: Bernd Schmidt", inner_text="Inner: Carla Weber")

    text = extract_docx(docx_bytes(doc))
    assert "Bernd Schmidt" in text
    assert "Carla Weber" in text


def test_extract_docx_walks_footnotes_and_endnotes():
    """PII in footnotes.xml / endnotes.xml must reach the detector."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Body")
    add_footnotes_part(doc, "Fussnote: Dora Klein")
    add_endnotes_part(doc, "Endnote: Emil Frank")

    text = extract_docx(docx_bytes(doc))
    assert "Dora Klein" in text
    assert "Emil Frank" in text


def test_extract_docx_walks_text_boxes_exactly_once():
    """Text-box content must be extracted — from one AlternateContent branch only."""
    from docx import Document

    doc = Document()
    add_textbox_paragraph(doc, "Box: Frieda Gross")

    text = extract_docx(docx_bytes(doc))
    # In the package the text exists twice (mc:Choice + mc:Fallback); the
    # extractor must see it once, not zero times and not twice.
    assert text.count("Frieda Gross") == 1


def test_extract_docx_walks_tracked_changes():
    """Inserted and deleted tracked text are both still in the file — extract both."""
    from docx import Document

    doc = Document()
    add_tracked_changes_paragraph(doc, inserted="Neu: Greta Held", deleted="Alt: Hans Igel")

    text = extract_docx(docx_bytes(doc))
    assert "Greta Held" in text
    assert "Hans Igel" in text


def test_extract_docx_keeps_deleted_text_a_separate_segment():
    """Deleted text must never merge into visible text (entities would straddle)."""
    from docx import Document

    doc = Document()
    add_tracked_changes_paragraph(doc, inserted="Anna", deleted="Mueller")

    text = extract_docx(docx_bytes(doc))
    assert "AnnaMueller" not in text
    assert "Anna" in text
    assert "Mueller" in text


def test_reconstruct_docx_scrubs_all_surfaces():
    """After redaction, no original name may survive anywhere in the raw XML."""
    from docx import Document

    doc = Document()
    doc.add_paragraph("Body: Carla Weber")
    add_block_sdt(doc, "Bearbeiter: Anna Mueller")
    add_nested_table(doc, outer_text="Outer: Bernd Schmidt", inner_text="Inner: Ines Jung")
    add_textbox_paragraph(doc, "Box: Dora Klein")
    add_tracked_changes_paragraph(doc, inserted="Neu: Emil Frank", deleted="Alt: Frieda Gross")
    add_footnotes_part(doc, "Fussnote: Greta Held")
    add_endnotes_part(doc, "Endnote: Hans Igel")

    names = [
        "Carla Weber",
        "Anna Mueller",
        "Bernd Schmidt",
        "Ines Jung",
        "Dora Klein",
        "Emil Frank",
        "Frieda Gross",
        "Greta Held",
        "Hans Igel",
    ]
    new_bytes = _redact(docx_bytes(doc), names)

    xml = all_xml(new_bytes)
    for name in names:
        assert name.encode() not in xml, f"{name!r} leaked into redacted DOCX"

    rewritten = extract_docx(new_bytes)
    # Deleted tracked text is stripped, not pseudonymized — every other
    # surface must carry its token.
    for i, name in enumerate(names, start=1):
        if name == "Frieda Gross":
            continue
        assert f"<<PERSON_{i}>>" in rewritten, f"token for {name!r} missing"


def test_reconstruct_docx_scrubs_fallback_branch():
    """mc:Fallback duplicates the text box; the rewrite must scrub both branches."""
    from docx import Document

    doc = Document()
    add_textbox_paragraph(doc, "Box: Dora Klein")

    new_bytes = _redact(docx_bytes(doc), ["Dora Klein"])
    assert b"Dora Klein" not in all_xml(new_bytes)


def test_reconstruct_docx_strips_deletions_even_without_detections():
    """PII living only in w:del must be stripped even when nothing was detected.

    Extraction shows deleted text to the detector, but the strip must not
    depend on a hit: a reconstruction with zero replacements previously
    returned the original bytes untouched.
    """
    from docx import Document

    doc = Document()
    doc.add_paragraph("Nothing detectable here.")
    add_tracked_changes_paragraph(doc, inserted="ok", deleted="Geheim: Hans Igel")
    data = docx_bytes(doc)

    extracted = extract_docx(data)
    block = FileBlock(
        content_bytes=data,
        mime_type=DOCX_MIME,
        source_path="test.docx",
        source_type="file",
        extracted_text=extracted,
        pseudonymized_text=extracted,
        entities=[],
    )
    new_bytes = _reconstruct_docx(block)
    assert new_bytes is not None
    assert b"Hans Igel" not in all_xml(new_bytes)


def test_reconstruct_docx_preserves_inline_pictures():
    """Rewriting a paragraph must not silently drop its inline picture."""
    import zipfile

    from docx import Document

    doc = Document()
    para = doc.add_paragraph()
    para.add_run("Unterschrift: Anna Mueller ")
    para.add_run().add_picture(io.BytesIO(TINY_PNG))

    new_bytes = _redact(docx_bytes(doc), ["Anna Mueller"])

    xml = all_xml(new_bytes)
    assert b"Anna Mueller" not in xml
    assert b"w:drawing" in xml

    with zipfile.ZipFile(io.BytesIO(new_bytes)) as zf:
        media = [n for n in zf.namelist() if n.startswith("word/media/")]
    assert media, "inline picture was dropped by the rewrite"

    rewritten = extract_docx(new_bytes)
    assert "<<PERSON_1>>" in rewritten
