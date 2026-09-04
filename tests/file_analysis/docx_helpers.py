"""Shared helpers for DOCX tests: in-code builders and raw-zip part injection.

Everything here is built in code — the repo keeps no binary fixtures.

Two families. python-docx has no API for content controls, nested tables, text
boxes, tracked changes or footnotes/endnotes, so those are hand-built XML
appended to a python-docx document. And it cannot *create*
``docProps/custom.xml``, ``word/people.xml`` or Manager/Company in ``app.xml``
— it only preserves them on round-trip — so those are spliced into a written
package, reusing the generic zip helpers from
:mod:`tests.file_analysis.xlsx_helpers`.
"""

from __future__ import annotations

import base64
import html
import io
import re
import zipfile
from xml.sax.saxutils import escape

from docx.document import Document as DocumentObject

from tests.file_analysis.xlsx_helpers import (
    _XML_HEAD,
    add_content_types,
    read_part,
    rewrite_zip,
)

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


_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_EXT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
_VT_NS = "http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes"
_CUSTOM_NS = "http://schemas.openxmlformats.org/officeDocument/2006/custom-properties"
_W15_NS = "http://schemas.microsoft.com/office/word/2012/wordml"

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

# A JPEG SOI marker followed by junk — enough to be "a thumbnail" for zip-level tests.
FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"NOIRDOC-FAKE-THUMBNAIL"


def document_bytes(doc: DocumentObject) -> bytes:
    """Serialize a python-docx document to bytes."""
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _append_before(xml: bytes, closing: bytes, insert: str) -> bytes:
    assert closing in xml, closing
    return xml.replace(closing, insert.encode() + closing, 1)


def strip_template_noise(data: bytes) -> bytes:
    """Remove the default-template parts that would otherwise dominate counts.

    python-docx's bundled template ships a ``docProps/thumbnail.jpeg``, a
    ``customXml/`` bibliography island and ``dc:creator`` "python-docx" —
    all of which the scrubber (rightly) reports. Tests that exercise other
    surfaces start from a package without them.
    """
    rels = read_part(data, "_rels/.rels")
    rels = re.sub(rb"<Relationship [^>]*thumbnail[^>]*/>", b"", rels)
    doc_rels = read_part(data, "word/_rels/document.xml.rels")
    doc_rels = re.sub(rb"<Relationship [^>]*customXml[^>]*/>", b"", doc_rels)
    core = read_part(data, "docProps/core.xml")
    core = core.replace(b"<dc:creator>python-docx</dc:creator>", b"<dc:creator/>")
    core = core.replace(
        b"<dc:description>generated by python-docx</dc:description>", b"<dc:description/>"
    )
    return rewrite_zip(
        data,
        {
            "_rels/.rels": rels,
            "word/_rels/document.xml.rels": doc_rels,
            "docProps/core.xml": core,
            "docProps/thumbnail.jpeg": None,
            "customXml/item1.xml": None,
            "customXml/_rels/item1.xml.rels": None,
            "customXml/itemProps1.xml": None,
        },
    )


def clean_document_bytes(doc: DocumentObject) -> bytes:
    """Serialize *doc* without the default-template noise parts."""
    return strip_template_noise(document_bytes(doc))


def inject_app_props(
    data: bytes,
    *,
    manager: str = "",
    company: str = "",
    hyperlink_base: str = "",
    titles_of_parts: list[str] | None = None,
) -> bytes:
    """Replace ``docProps/app.xml`` with one carrying the given fields."""
    titles = titles_of_parts or []
    lpstrs = "".join(f"<vt:lpstr>{html.escape(t, quote=False)}</vt:lpstr>" for t in titles)
    app = (
        f'{_XML_HEAD}<Properties xmlns="{_EXT_NS}" xmlns:vt="{_VT_NS}">'
        f"<Application>Microsoft Word</Application>"
        f"<Manager>{html.escape(manager, quote=False)}</Manager>"
        f"<Company>{html.escape(company, quote=False)}</Company>"
        f"<HyperlinkBase>{html.escape(hyperlink_base, quote=False)}</HyperlinkBase>"
        f'<TitlesOfParts><vt:vector size="{len(titles)}" baseType="lpstr">{lpstrs}</vt:vector>'
        f"</TitlesOfParts></Properties>"
    )
    return rewrite_zip(data, {"docProps/app.xml": app.encode()})


def inject_custom_props(data: bytes, props: dict[str, str | int]) -> bytes:
    """Add a ``docProps/custom.xml`` part (str -> lpwstr, int -> i4)."""
    entries = []
    for pid, (name, value) in enumerate(props.items(), start=2):
        if isinstance(value, int):
            body = f"<vt:i4>{value}</vt:i4>"
        else:
            body = f"<vt:lpwstr>{html.escape(value, quote=False)}</vt:lpwstr>"
        entries.append(
            f'<property fmtid="{{D5CDD505-2E9C-101B-9397-08002B2CF9AE}}" pid="{pid}" '
            f'name="{html.escape(name, quote=True)}">{body}</property>'
        )
    custom = (
        f'{_XML_HEAD}<Properties xmlns="{_CUSTOM_NS}" xmlns:vt="{_VT_NS}">'
        + "".join(entries)
        + "</Properties>"
    )
    rels = _append_before(
        read_part(data, "_rels/.rels"),
        b"</Relationships>",
        f'<Relationship Id="rIdCustomProps" Type="{_REL}/custom-properties" '
        'Target="docProps/custom.xml"/>',
    )
    out = rewrite_zip(data, {"docProps/custom.xml": custom.encode(), "_rels/.rels": rels})
    return add_content_types(
        out,
        [
            (
                "/docProps/custom.xml",
                "application/vnd.openxmlformats-officedocument.custom-properties+xml",
            )
        ],
    )


def inject_people(data: bytes, *, author: str, user_id: str, provider_id: str = "AD") -> bytes:
    """Add a ``word/people.xml`` part (modern comment identities)."""
    people = (
        f'{_XML_HEAD}<w15:people xmlns:w15="{_W15_NS}">'
        f'<w15:person w15:author="{html.escape(author, quote=True)}">'
        f'<w15:presenceInfo w15:providerId="{html.escape(provider_id, quote=True)}" '
        f'w15:userId="{html.escape(user_id, quote=True)}"/></w15:person></w15:people>'
    )
    doc_rels = _append_before(
        read_part(data, "word/_rels/document.xml.rels"),
        b"</Relationships>",
        '<Relationship Id="rIdPeople" '
        'Type="http://schemas.microsoft.com/office/2011/relationships/people" '
        'Target="people.xml"/>',
    )
    out = rewrite_zip(
        data, {"word/people.xml": people.encode(), "word/_rels/document.xml.rels": doc_rels}
    )
    return add_content_types(
        out,
        [
            (
                "/word/people.xml",
                "application/vnd.openxmlformats-officedocument.wordprocessingml.people+xml",
            )
        ],
    )


def inject_thumbnail(data: bytes, content: bytes = FAKE_JPEG) -> bytes:
    """Add (or replace) ``docProps/thumbnail.jpeg`` plus its package relationship."""
    rels = read_part(data, "_rels/.rels")
    if b"thumbnail" not in rels:
        rels = _append_before(
            rels,
            b"</Relationships>",
            '<Relationship Id="rIdThumb" '
            f'Type="{_NS_PKG}/metadata/thumbnail" Target="docProps/thumbnail.jpeg"/>',
        )
    return rewrite_zip(data, {"docProps/thumbnail.jpeg": content, "_rels/.rels": rels})


def inject_custom_xml(data: bytes, *, xml_text: str) -> bytes:
    """Add a ``customXml/`` data island referenced from the main document."""
    item = f"{_XML_HEAD}{xml_text}"
    item_props = (
        f'{_XML_HEAD}<ds:datastoreItem ds:itemID="{{11111111-2222-3333-4444-555555555555}}" '
        'xmlns:ds="http://schemas.openxmlformats.org/officeDocument/2006/customXml">'
        "<ds:schemaRefs/></ds:datastoreItem>"
    )
    item_rels = (
        f'{_XML_HEAD}<Relationships xmlns="{_NS_PKG}"><Relationship Id="rId1" '
        f'Type="{_REL}/customXmlProps" Target="itemProps9.xml"/></Relationships>'
    )
    doc_rels = _append_before(
        read_part(data, "word/_rels/document.xml.rels"),
        b"</Relationships>",
        f'<Relationship Id="rIdCX9" Type="{_REL}/customXml" Target="../customXml/item9.xml"/>',
    )
    out = rewrite_zip(
        data,
        {
            "customXml/item9.xml": item.encode(),
            "customXml/_rels/item9.xml.rels": item_rels.encode(),
            "customXml/itemProps9.xml": item_props.encode(),
            "word/_rels/document.xml.rels": doc_rels,
        },
    )
    return add_content_types(
        out,
        [
            (
                "/customXml/itemProps9.xml",
                "application/vnd.openxmlformats-officedocument.customXmlProperties+xml",
            )
        ],
    )


def add_hyperlink(para: object, text: str, url: str) -> None:
    """Append a real external ``w:hyperlink`` (one run) to *para*.

    python-docx can read hyperlinks but not create them, so the element and
    its relationship are built by hand.
    """
    from docx.opc.constants import RELATIONSHIP_TYPE
    from docx.oxml.ns import qn
    from docx.oxml.parser import OxmlElement

    r_id = para.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)  # type: ignore[attr-defined]
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    hyperlink.append(run)
    para._p.append(hyperlink)  # type: ignore[attr-defined]


def hyperlink_targets(data: bytes) -> list[str]:
    """Every external hyperlink ``Target`` in the package, sorted."""
    targets = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in zf.namelist():
            if not name.endswith(".rels"):
                continue
            for fragment in zf.read(name).decode().split("<Relationship")[1:]:
                if "/hyperlink" in fragment and 'Target="' in fragment:
                    targets.append(fragment.split('Target="')[1].split('"')[0])
    return sorted(targets)
