"""Shared helpers for XLSX tests: a deterministic detector and workbook builders.

Everything here is in-code — the repo keeps no binary fixtures.
"""

from __future__ import annotations

import html
import io
import re
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


# ── raw-zip part injection ───────────────────────────────
#
# openpyxl cannot *create* pivot tables, threaded comments or persons parts, only
# preserve/drop them on round-trip. Tests therefore splice minimal but valid parts
# into a workbook openpyxl wrote.

_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_R = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG = "http://schemas.openxmlformats.org/package/2006/relationships"
_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml"
_XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'


def rewrite_zip(xlsx: bytes, edits: dict[str, bytes | None]) -> bytes:
    """Return a copy of *xlsx* with parts replaced/added (bytes) or removed (``None``)."""
    out = io.BytesIO()
    with (
        zipfile.ZipFile(io.BytesIO(xlsx)) as zin,
        zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zout,
    ):
        seen: set[str] = set()
        for name in zin.namelist():
            seen.add(name)
            if name in edits:
                if edits[name] is not None:
                    zout.writestr(name, edits[name])  # type: ignore[arg-type]
                continue
            zout.writestr(name, zin.read(name))
        for name, data in edits.items():
            if name not in seen and data is not None:
                zout.writestr(name, data)
    return out.getvalue()


def _append_before(xml: bytes, closing: bytes, insert: str) -> bytes:
    assert closing in xml, closing
    return xml.replace(closing, insert.encode() + closing, 1)


def _relationships(xlsx: bytes, name: str) -> bytes:
    try:
        return read_part(xlsx, name)
    except KeyError:
        return f'{_XML_HEAD}<Relationships xmlns="{_NS_PKG}"></Relationships>'.encode()


def add_content_types(xlsx: bytes, overrides: list[tuple[str, str]]) -> bytes:
    ct = read_part(xlsx, "[Content_Types].xml")
    for part, content_type in overrides:
        ct = _append_before(
            ct, b"</Types>", f'<Override PartName="{part}" ContentType="{content_type}"/>'
        )
    return rewrite_zip(xlsx, {"[Content_Types].xml": ct})


PivotItem = tuple[str, object]  # ("x", index) | ("n", number) | ("s", inline string)


def inject_pivot(
    xlsx: bytes,
    *,
    refreshed_by: str,
    fields: list[tuple[str, list[str] | None]],
    records: list[list[PivotItem]],
    source_sheet: str = "Daten",
    source_ref: str = "A1:B3",
    sheet_part: str = "sheet1",
    tables: int = 1,
    cache_id: int = 1,
    captions: dict[str, str] | None = None,
    group_items: list[str] | None = None,
    table_sheets: list[str] | None = None,
) -> bytes:
    """Splice one pivot cache (+ ``tables`` pivot tables sharing it) into *xlsx*.

    ``fields`` = ``(cacheField name, shared string items or None for numeric)``;
    ``records`` = rows of typed items. The first field is the row axis, the second
    the data field — enough for openpyxl to load and re-serialize the pivot.
    ``captions`` adds ``c="…"`` to matching shared items; ``group_items`` adds a
    ``fieldGroup/groupItems`` list to the first field; ``table_sheets`` places
    table *n* on ``table_sheets[n-1]`` (default: all on ``sheet_part``).
    """
    cache_fields = []
    for index, (name, shared) in enumerate(fields):
        if shared is None:
            items = (
                '<sharedItems containsSemiMixedTypes="0" containsString="0" '
                'containsNumber="1" containsInteger="1" minValue="0" maxValue="0"/>'
            )
        elif shared:
            entries = []
            for s in shared:
                caption = captions.get(s) if captions else None
                cap = f' c="{html.escape(caption, quote=True)}"' if caption else ""
                entries.append(f'<s v="{html.escape(s, quote=True)}"{cap}/>')
            items = f'<sharedItems count="{len(shared)}">' + "".join(entries) + "</sharedItems>"
        else:
            items = "<sharedItems/>"
        group = ""
        if index == 0 and group_items:
            group = (
                f'<fieldGroup base="0"><groupItems count="{len(group_items)}">'
                + "".join(f'<s v="{html.escape(g, quote=True)}"/>' for g in group_items)
                + "</groupItems></fieldGroup>"
            )
        cache_fields.append(
            f'<cacheField name="{html.escape(name, quote=True)}" numFmtId="0">'
            f"{items}{group}</cacheField>"
        )

    def _item(kind: str, value: object) -> str:
        if kind == "s":
            return f'<s v="{html.escape(str(value), quote=True)}"/>'
        return f'<{kind} v="{value}"/>'

    record_rows = "".join("<r>" + "".join(_item(k, v) for k, v in row) + "</r>" for row in records)

    cache_def = (
        f'{_XML_HEAD}<pivotCacheDefinition xmlns="{_NS_MAIN}" xmlns:r="{_NS_R}" r:id="rId1" '
        f'refreshedBy="{html.escape(refreshed_by, quote=True)}" refreshedDate="45000" '
        f'createdVersion="6" refreshedVersion="6" minRefreshableVersion="3" '
        f'recordCount="{len(records)}">'
        f'<cacheSource type="worksheet"><worksheetSource ref="{source_ref}" sheet="{source_sheet}"/></cacheSource>'
        f'<cacheFields count="{len(fields)}">{"".join(cache_fields)}</cacheFields>'
        f"</pivotCacheDefinition>"
    )
    cache_rec = (
        f'{_XML_HEAD}<pivotCacheRecords xmlns="{_NS_MAIN}" xmlns:r="{_NS_R}" '
        f'count="{len(records)}">{record_rows}</pivotCacheRecords>'
    )
    cache_rels = (
        f'{_XML_HEAD}<Relationships xmlns="{_NS_PKG}"><Relationship Id="rId1" '
        f'Type="{_REL}/pivotCacheRecords" Target="pivotCacheRecords{cache_id}.xml"/></Relationships>'
    )

    first_shared = fields[0][1] or []
    row_items = (
        "".join(f'<item x="{i}"/>' for i in range(len(first_shared))) + '<item t="default"/>'
    )
    row_axis_items = (
        "".join(f'<i><x v="{i}"/></i>' if i else "<i><x/></i>" for i in range(len(first_shared)))
        + '<i t="grand"><x/></i>'
    )

    def _table(n: int) -> str:
        return (
            f'{_XML_HEAD}<pivotTableDefinition xmlns="{_NS_MAIN}" name="PivotTable{n}" '
            f'cacheId="{cache_id}" dataCaption="Werte" updatedVersion="6" minRefreshableVersion="3" '
            f'useAutoFormatting="1" itemPrintTitles="1" createdVersion="6" indent="0" outline="1" '
            f'outlineData="1" multipleFieldFilters="0">'
            f'<location ref="D{1 + (n - 1) * 10}:E{4 + (n - 1) * 10}" firstHeaderRow="1" firstDataRow="1" firstDataCol="1"/>'
            f'<pivotFields count="{len(fields)}"><pivotField axis="axisRow" showAll="0">'
            f'<items count="{len(first_shared) + 1}">{row_items}</items></pivotField>'
            + "".join('<pivotField dataField="1" showAll="0"/>' for _ in fields[1:])
            + '</pivotFields><rowFields count="1"><field x="0"/></rowFields>'
            f'<rowItems count="{len(first_shared) + 1}">{row_axis_items}</rowItems>'
            '<colItems count="1"><i/></colItems>'
            f'<dataFields count="1"><dataField name="Summe von {html.escape(fields[1][0], quote=True)}" '
            'fld="1" baseField="0" baseItem="0"/></dataFields>'
            '<pivotTableStyleInfo name="PivotStyleLight16" showRowHeaders="1" showColHeaders="1" '
            'showRowStripes="0" showColStripes="0" showLastColumn="1"/></pivotTableDefinition>'
        )

    table_rels = (
        f'{_XML_HEAD}<Relationships xmlns="{_NS_PKG}"><Relationship Id="rId1" '
        f'Type="{_REL}/pivotCacheDefinition" Target="../pivotCache/pivotCacheDefinition{cache_id}.xml"/>'
        "</Relationships>"
    )

    edits: dict[str, bytes | None] = {
        f"xl/pivotCache/pivotCacheDefinition{cache_id}.xml": cache_def.encode(),
        f"xl/pivotCache/_rels/pivotCacheDefinition{cache_id}.xml.rels": cache_rels.encode(),
        f"xl/pivotCache/pivotCacheRecords{cache_id}.xml": cache_rec.encode(),
    }
    overrides = [
        (f"/xl/pivotCache/pivotCacheDefinition{cache_id}.xml", f"{_CT}.pivotCacheDefinition+xml"),
        (f"/xl/pivotCache/pivotCacheRecords{cache_id}.xml", f"{_CT}.pivotCacheRecords+xml"),
    ]
    sheets = table_sheets or [sheet_part] * tables
    rels_by_sheet: dict[str, bytes] = {}
    for n, part in enumerate(sheets, start=1):
        edits[f"xl/pivotTables/pivotTable{n}.xml"] = _table(n).encode()
        edits[f"xl/pivotTables/_rels/pivotTable{n}.xml.rels"] = table_rels.encode()
        overrides.append((f"/xl/pivotTables/pivotTable{n}.xml", f"{_CT}.pivotTable+xml"))
        rels = rels_by_sheet.get(part) or _relationships(
            xlsx, f"xl/worksheets/_rels/{part}.xml.rels"
        )
        rels_by_sheet[part] = _append_before(
            rels,
            b"</Relationships>",
            f'<Relationship Id="rIdPT{n}" Type="{_REL}/pivotTable" Target="../pivotTables/pivotTable{n}.xml"/>',
        )
    for part, rels in rels_by_sheet.items():
        edits[f"xl/worksheets/_rels/{part}.xml.rels"] = rels

    workbook_xml = _append_before(
        read_part(xlsx, "xl/workbook.xml"),
        b"</workbook>",
        f'<pivotCaches><pivotCache xmlns:r="{_NS_R}" cacheId="{cache_id}" r:id="rIdPC{cache_id}"/></pivotCaches>',
    )
    edits["xl/workbook.xml"] = workbook_xml
    edits["xl/_rels/workbook.xml.rels"] = _append_before(
        read_part(xlsx, "xl/_rels/workbook.xml.rels"),
        b"</Relationships>",
        f'<Relationship Id="rIdPC{cache_id}" Type="{_REL}/pivotCacheDefinition" '
        f'Target="pivotCache/pivotCacheDefinition{cache_id}.xml"/>',
    )
    return add_content_types(rewrite_zip(xlsx, edits), overrides)


def inject_threaded_comment_parts(xlsx: bytes, *, display_name: str, text: str) -> bytes:
    """Add Excel-365 ``xl/persons`` + ``xl/threadedComments`` parts (openpyxl has no support)."""
    person_ns = "http://schemas.microsoft.com/office/spreadsheetml/2018/threadedcomments"
    persons = (
        f'{_XML_HEAD}<personList xmlns="{person_ns}"><person displayName="{html.escape(display_name, quote=True)}" '
        'id="{11111111-2222-3333-4444-555555555555}" userId="user@example.com" providerId="PeoplePicker"/></personList>'
    )
    threaded = (
        f'{_XML_HEAD}<ThreadedComments xmlns="{person_ns}"><threadedComment ref="A2" '
        'dT="2024-01-01T00:00:00Z" personId="{11111111-2222-3333-4444-555555555555}" '
        f'id="{{66666666-7777-8888-9999-000000000000}}"><text>{html.escape(text, quote=False)}</text>'
        "</threadedComment></ThreadedComments>"
    )
    out = rewrite_zip(
        xlsx,
        {
            "xl/persons/person.xml": persons.encode(),
            "xl/threadedComments/threadedComment1.xml": threaded.encode(),
        },
    )
    return add_content_types(
        out,
        [
            ("/xl/persons/person.xml", "application/vnd.ms-excel.person+xml"),
            (
                "/xl/threadedComments/threadedComment1.xml",
                "application/vnd.ms-excel.threadedcomments+xml",
            ),
        ],
    )


def strip_core_creator(xlsx: bytes) -> bytes:
    """Remove the ``<dc:creator>`` element from ``docProps/core.xml`` (foreign producers omit it)."""
    core = read_part(xlsx, "docProps/core.xml")
    stripped = re.sub(rb"<dc:creator\b[^>]*>.*?</dc:creator>", b"", core, flags=re.DOTALL)
    assert stripped != core, "fixture: core.xml had no dc:creator to strip"
    return rewrite_zip(xlsx, {"docProps/core.xml": stripped})


def inject_app_props(xlsx: bytes, *, manager: str, company: str) -> bytes:
    """Replace ``docProps/app.xml`` with one carrying Manager/Company (openpyxl rebuilds it blank)."""
    app_ns = "http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"
    app = (
        f'{_XML_HEAD}<Properties xmlns="{app_ns}"><Application>Microsoft Excel</Application>'
        f"<Manager>{html.escape(manager, quote=False)}</Manager>"
        f"<Company>{html.escape(company, quote=False)}</Company></Properties>"
    )
    return rewrite_zip(xlsx, {"docProps/app.xml": app.encode()})
