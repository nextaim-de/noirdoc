"""Pseudonymize and reveal DOCX package parts outside the document body.

``_reconstruct_docx`` only rewrites paragraph runs (body, headers/footers,
comment text). python-docx preserves every other package part verbatim, so a
redacted document still leaked: ``docProps/core.xml`` (creator,
lastModifiedBy, title, …), ``docProps/app.xml`` (Manager, Company, the
TitlesOfParts heading cache), ``docProps/custom.xml``, comment ``w:author`` /
``w:initials``, ``word/people.xml`` (modern comment identities incl. AD
userIds), the ``docProps/thumbnail.jpeg`` preview of the *original* page,
``customXml/`` data islands, and the ``mailto:`` targets of hyperlink
relationships — the address a redacted footer link still pointed at even once
its display text read ``<<EMAIL_1>>``.

Design mirrors :mod:`noirdoc.file_analysis.xlsx_parts`: **one** slot
enumerator (:func:`iter_text_slots`) feeds **both** directions —
:func:`pseudonymize_document_parts` and :func:`reidentify_document_parts` —
so the redact and reveal walkers cannot drift apart. Every text value is
pseudonymized through the shared mapper (never blanked), which keeps
``noirdoc reveal`` a full round-trip.

Two part families cannot be mapped and are **dropped** (counted, not
reversible):

* ``thumbnail`` (any package-level thumbnail relationship): a rendered image
  of the original, unredacted document. It cannot be pseudonymized.
* ``customXml/`` items: arbitrary schema-less data islands (DMS metadata,
  cover-page properties, mail-merge payloads). Scrubbing them selectively
  would need to understand every producer's schema; dropping is the safe
  choice. Word keeps showing the last rendered text of content controls bound
  to a dropped island, and that text goes through the body redaction pass.

An unparseable ``app.xml`` / ``custom.xml`` / ``people.xml`` is also dropped
rather than passed through verbatim — a corrupt part must not become a leak.

Surface names reported in ``classifications`` never carry file-provided
strings other than property names; persons and customXml items are referred
to by ordinal. Classifications are logged.
"""

from __future__ import annotations

import asyncio
import io
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import quote, unquote

import structlog

# Entity-safe parser/serializer (resolve_entities=False under lxml); openpyxl
# is a hard dependency and xlsx_parts uses the same functions.
from openpyxl.xml.functions import fromstring, tostring

from noirdoc.file_analysis.xlsx_parts import PartsResult

if TYPE_CHECKING:
    from docx.document import Document as DocumentObject

    from noirdoc.detection.base import DetectedEntity
    from noirdoc.file_analysis.xlsx_inference import DetectorLike
    from noirdoc.pseudonymization.mapper import PseudonymMapper
    from noirdoc.reidentification.engine import ReidentificationEngine

logger = structlog.get_logger()


class DocxPartsError(RuntimeError):
    """The DOCX package could not be opened for the part-level scrub.

    Raised for zip-safety rejections and corrupt packages. Callers must fail
    closed: the body may already be redacted, but ``docProps``, comment authors
    and ``people.xml`` were never touched, so the bytes must not go out as a
    finished redaction. Mirrors
    :class:`~noirdoc.file_analysis.xlsx_inference.XlsxLoadError`.
    """


_PLACEHOLDER = re.compile(r"<<[A-Z_]+_\d+>>")

# core.xml fields that are a person (or account) by definition — never rely on NER.
_CORE_AUTHOR_FIELDS = ("author", "last_modified_by")
# core.xml free-text fields — run through the detector like body text.
# ("comments" is the dc:description field, not review comments.)
_CORE_FREE_FIELDS = (
    "title",
    "subject",
    "comments",
    "keywords",
    "category",
    "content_status",
    "identifier",
)

_EXT_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/extended-properties}"
_VT_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/docPropsVTypes}"
_CUSTOM_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/custom-properties}"
_W15_NS = "{http://schemas.microsoft.com/office/word/2012/wordml}"

_APP_PARTNAME = "/docProps/app.xml"
_CUSTOM_PARTNAME = "/docProps/custom.xml"
_PEOPLE_PARTNAME = "/word/people.xml"
_SCRUBBED_PARTNAMES = (_APP_PARTNAME, _CUSTOM_PARTNAME, _PEOPLE_PARTNAME)

_THUMBNAIL_RELTYPE_SUFFIX = "/metadata/thumbnail"
_CUSTOM_XML_RELTYPE_SUFFIX = "/customXml"
_HYPERLINK_RELTYPE_SUFFIX = "/hyperlink"
_MAILTO = "mailto:"

SlotKind = Literal["author", "org", "free"]

# (app.xml element, slot kind) — same schema as the XLSX app.xml the 0.1.3
# work counts; here python-docx preserves the part, so both are reversible.
_APP_FIELDS: tuple[tuple[str, SlotKind], ...] = (("Manager", "author"), ("Company", "org"))


@dataclass
class _Slot:
    """One string-bearing attribute somewhere in the document package."""

    surface: str  # human-readable location, e.g. "core.author"
    kind: SlotKind
    obj: Any  # object holding the string
    attr: str  # attribute name on ``obj``
    count: bool = True  # False for derived duplicates (comment initials)

    @property
    def value(self) -> str | None:
        """Current string, or ``None`` when the slot is empty / not text."""
        raw = getattr(self.obj, self.attr, None)
        if isinstance(raw, str) and raw.strip():
            return raw
        return None

    def set(self, value: str) -> None:
        setattr(self.obj, self.attr, value)


class _PartXml:
    """Parsed XML of a generic OPC part; every write re-serializes the blob."""

    def __init__(self, part: Any, root: Any) -> None:
        self._part = part
        self.root = root

    def flush(self) -> None:
        self._part._blob = tostring(self.root, xml_declaration=True, encoding="UTF-8")


class _ElementText:
    """An element's text content, exposed as a ``value`` attribute."""

    def __init__(self, xml: _PartXml, elem: Any) -> None:
        self._xml = xml
        self._elem = elem

    @property
    def value(self) -> Any:
        return self._elem.text

    @value.setter
    def value(self, new: str) -> None:
        self._elem.text = new
        self._xml.flush()


class _ElementAttr:
    """An element's (namespaced) attribute, exposed as a ``value`` attribute."""

    def __init__(self, xml: _PartXml, elem: Any, qname: str) -> None:
        self._xml = xml
        self._elem = elem
        self._qname = qname

    @property
    def value(self) -> Any:
        return self._elem.get(self._qname)

    @value.setter
    def value(self, new: str) -> None:
        self._elem.set(self._qname, new)
        self._xml.flush()


class _MailtoTarget:
    """An external hyperlink's ``mailto:`` target, exposed as its plain address.

    Mirrors :class:`noirdoc.file_analysis.xlsx_parts._MailtoTarget`. ``<`` and
    ``>`` are illegal URI characters, so a raw ``<<EMAIL_1>>`` in a
    relationship ``Target`` would risk Word's repair prompt; the placeholder is
    percent-encoded on write (``mailto:%3C%3CEMAIL_1%3E%3E`` — RFC 6068 allows
    percent-encoding in mailto URIs) and decoded again by the getter, so redact
    and reveal both see the plain placeholder.

    A target carrying a query (``?subject=…``) is mapped whole rather than
    split: the query can hold PII of its own, and mapping the whole string
    keeps the round-trip exact. The common query-less case decodes to the bare
    address, so it shares the placeholder the link *text* already got.

    python-docx exposes ``target_ref`` read-only; ``_target`` is the only
    writable handle for an external relationship.
    """

    def __init__(self, rel: Any) -> None:
        self._rel = rel

    @property
    def address(self) -> str | None:
        target = self._rel.target_ref
        if isinstance(target, str) and target.lower().startswith(_MAILTO):
            return unquote(target[len(_MAILTO) :])
        return None

    @address.setter
    def address(self, value: str) -> None:
        # "@?=&" keeps the address and any ?subject= query structure intact.
        self._rel._target = _MAILTO + quote(value, safe="@?=&")


def _hyperlink_slots(doc: DocumentObject) -> Iterator[_Slot]:
    """``mailto:`` targets of every external hyperlink relationship.

    The address lives in ``word/_rels/*.rels``, not in the body, so the text
    rewrite never reaches it: a redacted footer whose link text is already
    ``<<EMAIL_1>>`` still carried ``mailto:info@musterfirma.de`` verbatim —
    one ``unzip -p`` away in a shipped document.

    Headers, footers, comments and the notes parts each own their rels, so the
    whole package is walked rather than just ``document.xml``. Non-``mailto:``
    targets are left alone: an ``https`` URL is not reliably PII and rewriting
    it would break the link.
    """
    package = doc.part.package
    for part in package.iter_parts():
        # Part names are structural OPC paths (``word/footer1.xml``), not
        # file-provided text, so they are safe to report as a surface.
        where = str(part.partname).lstrip("/")
        for rid in sorted(part.rels):
            rel = part.rels[rid]
            if not rel.is_external or not rel.reltype.endswith(_HYPERLINK_RELTYPE_SUFFIX):
                continue
            target = _MailtoTarget(rel)
            if target.address is None:
                continue  # https, file, anchor — not an address we can map
            yield _Slot(f"hyperlink.mailto@{where}!{rid}", "author", target, "address")


def _find_scrubbed_parts(doc: DocumentObject) -> list[tuple[str, Any]]:
    """The generic XML parts this module rewrites, as ``(partname, part)``."""
    found: list[tuple[str, Any]] = []
    for part in doc.part.package.iter_parts():
        name = str(part.partname)
        if name in _SCRUBBED_PARTNAMES:
            found.append((name, part))
    return found


def _parse_part(name: str, part: Any) -> _PartXml | None:
    try:
        return _PartXml(part, fromstring(part.blob))
    except Exception as exc:
        logger.warning("docx_parts.part_unreadable", part=name, error=str(exc))
        return None


def _app_slots(xml: _PartXml) -> Iterator[_Slot]:
    for tag, kind in _APP_FIELDS:
        elem = xml.root.find(f"{_EXT_NS}{tag}")
        if elem is not None:
            yield _Slot(f"app.{tag}", kind, _ElementText(xml, elem), "value")
    elem = xml.root.find(f"{_EXT_NS}HyperlinkBase")
    if elem is not None:
        yield _Slot("app.HyperlinkBase", "free", _ElementText(xml, elem), "value")
    # Word caches the document's heading titles here — including the title that
    # core.title / the body pass already pseudonymized.
    titles = xml.root.find(f"{_EXT_NS}TitlesOfParts")
    if titles is not None:
        for index, lpstr in enumerate(titles.iter(f"{_VT_NS}lpstr"), start=1):
            yield _Slot(f"app.TitlesOfParts.{index}", "free", _ElementText(xml, lpstr), "value")


def _custom_slots(xml: _PartXml) -> Iterator[_Slot]:
    for prop in xml.root.iter(f"{_CUSTOM_NS}property"):
        name = prop.get("name") or "?"
        for vt_tag in ("lpwstr", "lpstr"):
            elem = prop.find(f"{_VT_NS}{vt_tag}")
            if elem is not None:
                yield _Slot(f"custom.{name}", "free", _ElementText(xml, elem), "value")
                break


def _people_slots(xml: _PartXml) -> Iterator[_Slot]:
    for ordinal, person in enumerate(xml.root.iter(f"{_W15_NS}person"), start=1):
        yield _Slot(
            f"people.person{ordinal}.author",
            "author",
            _ElementAttr(xml, person, f"{_W15_NS}author"),
            "value",
        )
        presence = person.find(f"{_W15_NS}presenceInfo")
        if presence is not None:
            yield _Slot(
                f"people.person{ordinal}.userId",
                "author",
                _ElementAttr(xml, presence, f"{_W15_NS}userId"),
                "value",
            )


_PART_SLOTS = {
    _APP_PARTNAME: _app_slots,
    _CUSTOM_PARTNAME: _custom_slots,
    _PEOPLE_PARTNAME: _people_slots,
}


def iter_text_slots(doc: DocumentObject) -> Iterator[_Slot]:
    """Yield every string slot outside the run text, in a deterministic order.

    Comment *text* is body content: the reconstruction/reveal walkers handle
    it as a block container. Here only the structured identity fields appear.
    """
    props = doc.core_properties
    for name in _CORE_AUTHOR_FIELDS:
        yield _Slot(f"core.{name}", "author", props, name)
    for name in _CORE_FREE_FIELDS:
        yield _Slot(f"core.{name}", "free", props, name)

    try:
        comments = list(doc.comments)
    except Exception:  # documents without a comments part
        comments = []
    for comment in comments:
        cid = comment.comment_id
        yield _Slot(f"comment{cid}.author", "author", comment, "author")
        # Initials duplicate the author identity; mapped, but not counted twice.
        yield _Slot(f"comment{cid}.initials", "author", comment, "initials", count=False)

    # After the comment authors, so the body pass and these have already taught
    # the mapper an address that also appears as link text.
    yield from _hyperlink_slots(doc)

    for name, part in _find_scrubbed_parts(doc):
        xml = _parse_part(name, part)
        if xml is not None:
            yield from _PART_SLOTS[name](xml)


def _author_entity_type(value: str) -> str:
    return "EMAIL" if "@" in value else "PERSON"


async def _detect_all(
    texts: list[str], detector: DetectorLike, language: str
) -> list[list[DetectedEntity]]:
    sem = asyncio.Semaphore(8)

    async def _one(text: str) -> list[DetectedEntity]:
        async with sem:
            return await detector.detect(text, language)

    return await asyncio.gather(*[_one(t) for t in texts])


def _drop_part(doc: DocumentObject, target: Any) -> bool:
    """Remove every relationship pointing at *target* so it is not written."""
    dropped = False
    for holder in (doc.part.package, doc.part):
        for rid, rel in list(holder.rels.items()):
            if not rel.is_external and rel.target_part is target:
                del holder.rels[rid]
                dropped = True
    return dropped


def _handle_unmappable_parts(doc: DocumentObject, result: PartsResult, *, apply: bool) -> bool:
    """Report (and with *apply*, drop) parts that cannot be pseudonymized.

    These land in ``result.dropped_parts``, never in ``entity_types``. Their
    presence says nothing about whether the document holds PII — python-docx's
    own default template ships a thumbnail and a customXml island, and so do
    most documents built from a corporate Word template. Counting them as
    entities would inflate every DOCX's entity count and reject every DOCX in
    block mode.
    """
    changed = False

    for rid, rel in list(doc.part.package.rels.items()):
        if rel.is_external or not rel.reltype.endswith(_THUMBNAIL_RELTYPE_SUFFIX):
            continue
        result._add_dropped("THUMBNAIL")
        result.classifications["docProps.thumbnail"] = "THUMBNAIL (dropped)"
        if apply:
            del doc.part.package.rels[rid]
            changed = True

    ordinal = 0
    for rid, rel in list(doc.part.rels.items()):
        if rel.is_external or not rel.reltype.endswith(_CUSTOM_XML_RELTYPE_SUFFIX):
            continue
        ordinal += 1
        result._add_dropped("CUSTOM_XML")
        result.classifications[f"customXml.item{ordinal}"] = "CUSTOM_XML (dropped)"
        if apply:
            del doc.part.rels[rid]
            changed = True

    # Fail closed: a part this module is supposed to rewrite but cannot parse
    # would otherwise pass through verbatim, PII included. Dropping it is a
    # precaution, not a finding — nothing was read, so nothing was found.
    for name, part in _find_scrubbed_parts(doc):
        if _parse_part(name, part) is not None:
            continue
        result._add_dropped("UNREADABLE_PART")
        result.classifications[name.lstrip("/")] = "UNREADABLE_PART (dropped)"
        if apply and _drop_part(doc, part):
            changed = True

    return changed


async def pseudonymize_document_parts(
    doc: DocumentObject,
    detector: DetectorLike,
    mapper: PseudonymMapper,
    language: str,
    *,
    apply: bool = True,
) -> tuple[PartsResult, bool]:
    """Pseudonymize every part-level string slot of *doc* in place.

    Returns ``(result, changed)``. With ``apply=False`` (detect-only / block
    modes) entities are detected and counted but nothing is written and the
    mapper is never touched.
    """
    from noirdoc.pseudonymization.engine import PseudonymizationEngine

    result = PartsResult()
    changed = False
    slots = [s for s in iter_text_slots(doc) if s.value is not None]

    # Detect each distinct free text once.
    free_indexes = [i for i, s in enumerate(slots) if s.kind == "free"]
    unique_texts: dict[str, int] = {}
    for i in free_indexes:
        text = slots[i].value
        if text is not None and text not in unique_texts:
            unique_texts[text] = len(unique_texts)
    unique_results = await _detect_all(list(unique_texts), detector, language)
    detected = {
        i: unique_results[unique_texts[text]]
        for i in free_indexes
        if (text := slots[i].value) is not None
    }
    engine = PseudonymizationEngine()

    for index, slot in enumerate(slots):
        value = slot.value
        if value is None:
            continue

        if slot.kind in ("author", "org"):
            if mapper.reverse_lookup(value) is not None:
                continue  # already one of this mapper's placeholders
            entity_type = "ORGANIZATION" if slot.kind == "org" else _author_entity_type(value)
            if apply:
                slot.set(mapper.get_or_create(value, entity_type))
                changed = True
            if slot.count:
                result._add(entity_type)
                result.classifications[slot.surface] = f"{entity_type} (forced)"
            continue

        entities = detected.get(index, [])
        if not entities:
            continue
        if apply:
            new_value = engine.pseudonymize(value, entities, mapper)
            if new_value != value:
                slot.set(new_value)
                changed = True
        for entity in entities:
            result._add(entity.entity_type)
        best = max(entities, key=lambda e: e.score)
        result.classifications[slot.surface] = f"{best.entity_type} (detected)"

    if _handle_unmappable_parts(doc, result, apply=apply):
        changed = True

    logger.debug(
        "docx_parts.completed",
        entity_types=result.entity_types,
        dropped_parts=result.dropped_parts,
        apply=apply,
    )
    return result, changed


async def pseudonymize_docx_parts(
    data: bytes,
    detector: DetectorLike,
    mapper: PseudonymMapper,
    language: str,
    *,
    apply: bool = True,
) -> tuple[bytes | None, PartsResult]:
    """Bytes-level wrapper: load *data*, scrub the package parts, re-save.

    Returns ``(new_bytes, result)``; ``new_bytes`` is ``None`` when nothing
    changed (or with ``apply=False``), so the caller keeps its input.

    Raises :class:`DocxPartsError` when the package cannot be loaded (zip-safety
    rejection, corrupt archive). Swallowing that would hand the caller a
    document whose body was redacted but whose ``docProps`` still name the
    author — reported as a clean redaction.
    """
    from docx import Document

    from noirdoc.file_analysis.extractors._zip_safety import check_ooxml_zip_safe

    result = PartsResult()
    try:
        check_ooxml_zip_safe(data, label="docx")
        doc = Document(io.BytesIO(data))
    except Exception as exc:
        logger.warning("docx_parts.load_failed", error=str(exc))
        raise DocxPartsError(f"cannot load docx package: {exc}") from exc

    result, changed = await pseudonymize_document_parts(
        doc, detector, mapper, language, apply=apply
    )
    if not (apply and changed):
        return None, result

    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue(), result


def reidentify_document_parts(
    doc: DocumentObject, engine: ReidentificationEngine, mapper: PseudonymMapper
) -> bool:
    """Reverse placeholders in every part-level slot. Returns ``True`` if changed."""
    changed = False
    for slot in iter_text_slots(doc):
        value = slot.value
        if value is None or not _PLACEHOLDER.search(value):
            continue
        revealed = engine.reidentify(value, mapper)
        if revealed != value:
            slot.set(revealed)
            changed = True
    return changed
