"""Shared helpers for DOCX tests: in-code builders for the blind-spot surfaces.

Everything here is built in code — the repo keeps no binary fixtures.
python-docx has no API for content controls, nested tables, text boxes,
tracked changes, or footnotes/endnotes, so those parts are hand-built XML
appended to a python-docx document.
"""

from __future__ import annotations

import base64
import io
import zipfile
from typing import TYPE_CHECKING
from xml.sax.saxutils import escape

if TYPE_CHECKING:
    from docx.document import Document as DocumentObject

W_XMLNS = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'

# 1x1 transparent PNG — smallest valid image python-docx accepts.
TINY_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8"
    "z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def docx_bytes(doc: DocumentObject) -> bytes:
    """Serialize a python-docx document to bytes."""
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def all_xml(data: bytes) -> bytes:
    """Concatenated bytes of every XML part in the package (leak scanning)."""
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        return b"".join(zf.read(name) for name in zf.namelist() if name.endswith(".xml"))


def add_block_sdt(doc: DocumentObject, text: str) -> None:
    """Append a block-level content control (w:sdt) containing *text*."""
    from docx.oxml import parse_xml

    doc.element.body.append(
        parse_xml(
            f"<w:sdt {W_XMLNS}><w:sdtPr/><w:sdtContent>"
            f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"
            f"</w:sdtContent></w:sdt>"
        )
    )


def add_nested_table(doc: DocumentObject, outer_text: str, inner_text: str) -> None:
    """Append a 1x1 table whose cell holds *outer_text* and a nested 1x1 table."""
    from docx.oxml import parse_xml

    table = doc.add_table(rows=1, cols=1)
    cell = table.rows[0].cells[0]
    cell.text = outer_text
    tc = cell._tc
    tc.append(
        parse_xml(
            f"<w:tbl {W_XMLNS}><w:tblPr/><w:tblGrid><w:gridCol/></w:tblGrid>"
            f"<w:tr><w:tc><w:tcPr/><w:p><w:r><w:t>{escape(inner_text)}</w:t></w:r></w:p></w:tc></w:tr>"
            f"</w:tbl>"
        )
    )
    # A w:tc must end with a w:p after a nested table.
    tc.append(parse_xml(f"<w:p {W_XMLNS}/>"))


def add_textbox_paragraph(doc: DocumentObject, text: str) -> None:
    """Append a paragraph holding a text box via mc:AlternateContent.

    Both the mc:Choice (DrawingML shape) and the mc:Fallback (VML pict)
    branch carry *text*, as Word writes them.
    """
    from docx.oxml import parse_xml

    doc.element.body.append(
        parse_xml(
            '<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" '
            'xmlns:mc="http://schemas.openxmlformats.org/markup-compatibility/2006" '
            'xmlns:wps="http://schemas.microsoft.com/office/word/2010/wordprocessingShape" '
            'xmlns:v="urn:schemas-microsoft-com:vml">'
            "<w:r><mc:AlternateContent>"
            '<mc:Choice Requires="wps"><w:drawing><wps:txbx><w:txbxContent>'
            f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"
            "</w:txbxContent></wps:txbx></w:drawing></mc:Choice>"
            "<mc:Fallback><w:pict><v:shape><v:textbox><w:txbxContent>"
            f"<w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p>"
            "</w:txbxContent></v:textbox></v:shape></w:pict></mc:Fallback>"
            "</mc:AlternateContent></w:r></w:p>"
        )
    )


def add_tracked_changes_paragraph(doc: DocumentObject, inserted: str, deleted: str) -> None:
    """Append a paragraph with a tracked insertion and a tracked deletion."""
    from docx.oxml import parse_xml

    doc.element.body.append(
        parse_xml(
            f"<w:p {W_XMLNS}>"
            f'<w:ins w:id="1" w:author="rev" w:date="2026-01-01T00:00:00Z">'
            f"<w:r><w:t>{escape(inserted)}</w:t></w:r></w:ins>"
            f'<w:del w:id="2" w:author="rev" w:date="2026-01-01T00:00:00Z">'
            f"<w:r><w:delText>{escape(deleted)}</w:delText></w:r></w:del>"
            f"</w:p>"
        )
    )


def _add_notes_part(
    doc: DocumentObject,
    *,
    partname: str,
    content_type: str,
    reltype: str,
    root_tag: str,
    note_tag: str,
    text: str,
) -> None:
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part

    xml = (
        f"<w:{root_tag} {W_XMLNS}>"
        f'<w:{note_tag} w:type="separator" w:id="-1">'
        f"<w:p><w:r><w:separator/></w:r></w:p></w:{note_tag}>"
        f'<w:{note_tag} w:id="1"><w:p><w:r><w:t>{escape(text)}</w:t></w:r></w:p></w:{note_tag}>'
        f"</w:{root_tag}>"
    ).encode()
    part = Part(PackURI(partname), content_type, xml, doc.part.package)
    doc.part.relate_to(part, reltype)


def add_footnotes_part(doc: DocumentObject, text: str) -> None:
    """Attach a footnotes part whose footnote 1 contains *text*."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    _add_notes_part(
        doc,
        partname="/word/footnotes.xml",
        content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.footnotes+xml"
        ),
        reltype=RT.FOOTNOTES,
        root_tag="footnotes",
        note_tag="footnote",
        text=text,
    )


def add_endnotes_part(doc: DocumentObject, text: str) -> None:
    """Attach an endnotes part whose endnote 1 contains *text*."""
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    _add_notes_part(
        doc,
        partname="/word/endnotes.xml",
        content_type=(
            "application/vnd.openxmlformats-officedocument.wordprocessingml.endnotes+xml"
        ),
        reltype=RT.ENDNOTES,
        root_tag="endnotes",
        note_tag="endnote",
        text=text,
    )
