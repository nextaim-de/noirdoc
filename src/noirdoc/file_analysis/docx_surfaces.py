"""Shared DOCX text-surface walker feeding extraction, redaction, and reveal.

Named for *text surfaces*, to keep it apart from
:mod:`noirdoc.file_analysis.docx_parts`, which scrubs the package parts around
the body (docProps, comment authors, people.xml).

``extract_docx`` used to walk ``doc.paragraphs`` / ``doc.tables`` through
python-docx's block-container API, which silently skips every other text
surface in the package: content controls (``w:sdt``), nested tables, text
boxes and shapes (``w:txbxContent`` inside ``mc:AlternateContent``), tracked
changes (``w:ins`` / ``w:del``) and the footnotes/endnotes parts. PII on
those surfaces was neither shown to the detector nor rewritten in the output.

Design: **one** walker enumerates the text nodes for all three directions —
extraction (:func:`extract_document_texts`), the redaction rewrite and the
reveal (:func:`rewrite_document_texts`) — so they cannot drift apart. The
walker operates on the raw XML: it visits every ``w:p`` in document order
(``w:sdt`` content, nested tables at any depth, and text-box paragraphs are
all reached this way) under the body, every distinct header/footer part,
every review comment, and the footnotes/endnotes parts.

Decisions baked in:

* **Tracked deletions** (``w:del`` / ``w:delText``): deleted text is still in
  the file, so extraction surfaces it to the detector — as a *separate*
  segment, never merged into the visible text, so a detected entity can
  never straddle the visible/deleted boundary. The redaction rewrite
  **strips** ``w:del`` content entirely (the safer choice: no deleted PII
  and no pseudonym for it survives in the output, whether or not the
  detector flagged it). The reveal rewrites ``w:delText`` in place.
* **Tracked insertions** (``w:ins``): inserted text is visible text; it is
  extracted and rewritten like any other run.
* **``mc:AlternateContent``**: extraction reads only the primary
  (non-``mc:Fallback``) branch to avoid double-extraction — the fallback
  duplicates the same content. Rewrites cover *both* branches so the
  fallback cannot leak originals.
* **Hyperlinks**: a ``w:hyperlink``'s text nodes form their own rewrite
  segment. The replacement therefore stays inside the link (a redacted
  address does not leak out of it) and the link's text is never moved into
  the surrounding runs. Detection still sees the whole paragraph, so an
  entity is found across the boundary even though it is rewritten per
  segment.
* **Inline pictures and other non-text run content**: rewrites only touch
  ``w:t`` / ``w:delText`` nodes, so ``w:drawing`` (and any other run child)
  survives. ``w:tab`` / ``w:br`` take part in the replacement as ``\\t`` /
  ``\\n`` and are re-materialized as elements afterwards — a literal tab or
  newline inside ``w:t`` is whitespace to Word, not a tab stop or a line
  break.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

    from docx.document import Document as DocumentObject

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_MC = "{http://schemas.openxmlformats.org/markup-compatibility/2006}"
_XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"

_W_P = f"{_W}p"
_W_PPR = f"{_W}pPr"
_W_RPR = f"{_W}rPr"
_W_T = f"{_W}t"
_W_DELTEXT = f"{_W}delText"
_W_DEL = f"{_W}del"
_W_TAB = f"{_W}tab"
_W_BR = f"{_W}br"
_W_CR = f"{_W}cr"
_W_HYPERLINK = f"{_W}hyperlink"
_MC_FALLBACK = f"{_MC}Fallback"

# Node kinds inside a paragraph group.
_TEXT = "t"
_DELETED = "del"
_TAB = "tab"
_BREAK = "br"

_SEPARATOR_CHARS = {_TAB: "\t", _BREAK: "\n"}
# Inverse, for putting separators back after a rewrite. python-docx's own
# Run.text setter maps "\r" to w:br too, so a w:cr round-trips as w:br.
_SEPARATOR_TAGS = {"\t": "w:tab", "\n": "w:br", "\r": "w:br"}
_SEPARATOR_SPLIT = re.compile(r"([\t\n\r])")

# (kind, element, enclosing w:hyperlink or None)
_Group = list[tuple[str, Any, Any]]


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_document_texts(doc: DocumentObject) -> list[str]:
    """Return every non-empty text segment of *doc*, one entry per paragraph.

    Covers the body (including content controls, nested tables, text boxes,
    tracked changes), all distinct header/footer parts, review comments, and
    the footnotes/endnotes parts. Deleted tracked text is appended as its own
    segment after the paragraph's visible text.
    """
    parts: list[str] = []
    for root in _iter_element_roots(doc):
        _extract_from_root(root, parts)
    for note_part in _iter_note_parts(doc):
        from docx.oxml import parse_xml

        _extract_from_root(parse_xml(note_part.blob), parts)
    return parts


def rewrite_document_texts(
    doc: DocumentObject,
    transform: Callable[[str], str],
    *,
    strip_deleted: bool,
) -> bool:
    """Apply *transform* to every text surface of *doc*; return ``True`` if changed.

    Walks exactly the surfaces :func:`extract_document_texts` extracts (plus
    ``mc:Fallback`` duplicates, so text boxes cannot leak originals through
    the fallback branch). *transform* receives one paragraph's concatenated
    visible text and returns the replacement text.

    With ``strip_deleted=True`` (redaction), every ``w:del`` element is
    removed and stray ``w:delText`` is blanked before rewriting. With
    ``strip_deleted=False`` (reveal), deleted text is transformed in place.
    """
    changed = False
    for root in _iter_element_roots(doc):
        if strip_deleted and _strip_tracked_deletions(root):
            changed = True
        if _rewrite_root(root, transform):
            changed = True

    from docx.opc.oxml import serialize_part_xml
    from docx.oxml import parse_xml

    for note_part in _iter_note_parts(doc):
        root = parse_xml(note_part.blob)
        part_changed = False
        if strip_deleted and _strip_tracked_deletions(root):
            part_changed = True
        if _rewrite_root(root, transform):
            part_changed = True
        if part_changed:
            note_part._blob = serialize_part_xml(root)
            changed = True
    return changed


# ---------------------------------------------------------------------------
# Container enumeration — the roots both directions walk
# ---------------------------------------------------------------------------


def _iter_element_roots(doc: DocumentObject) -> Iterator[Any]:
    """Yield the element root of every in-document text container.

    Body first, then each *distinct* header/footer part (linked headers
    resolve to the previous section's part — deduplicated by element
    identity), then every review comment.
    """
    yield doc.element.body

    seen: set[int] = set()
    for section in doc.sections:
        for hdrftr in (
            section.header,
            section.first_page_header,
            section.even_page_header,
            section.footer,
            section.first_page_footer,
            section.even_page_footer,
        ):
            try:
                element = hdrftr._element
            except Exception:  # pragma: no cover - malformed sectPr
                continue
            if id(element) in seen:
                continue
            seen.add(id(element))
            yield element

    try:
        comments = list(doc.comments)
    except Exception:  # pragma: no cover - malformed comments part
        comments = []
    for comment in comments:
        yield comment._element


def _iter_note_parts(doc: DocumentObject) -> Iterator[Any]:
    """Yield the footnotes and endnotes OPC parts, when present.

    python-docx has no object API for these; they are generic parts whose
    XML we parse and (on rewrite) serialize back into the part blob.
    """
    from docx.opc.constants import RELATIONSHIP_TYPE as RT

    for reltype in (RT.FOOTNOTES, RT.ENDNOTES):
        try:
            yield doc.part.part_related_by(reltype)
        except KeyError:
            continue


# ---------------------------------------------------------------------------
# Paragraph-group walker
# ---------------------------------------------------------------------------


def _iter_paragraph_groups(root: Any, *, include_fallback: bool) -> Iterator[_Group]:
    """Yield one group of text-bearing nodes per ``w:p`` under *root*.

    A group lists, in document order, the paragraph's *own* ``w:t`` /
    ``w:delText`` nodes plus ``w:tab`` / ``w:br`` / ``w:cr`` separators.
    Paragraphs nested inside the paragraph (text-box content) form their own
    groups. With ``include_fallback=False``, everything under
    ``mc:Fallback`` is skipped so duplicated text-box content is not
    extracted twice.
    """
    for para in root.iter(_W_P):
        if not include_fallback and _under_fallback(para, root):
            continue
        group: _Group = []
        _collect_own_nodes(para, group, include_fallback=include_fallback)
        if group:
            yield group


def _under_fallback(element: Any, root: Any) -> bool:
    parent = element.getparent()
    while parent is not None and parent is not root:
        if parent.tag == _MC_FALLBACK:
            return True
        parent = parent.getparent()
    return False


def _collect_own_nodes(
    element: Any, group: _Group, *, include_fallback: bool, link: Any = None
) -> None:
    for child in element:
        tag = child.tag
        if tag == _W_P:
            continue  # nested paragraph (text box) — walked as its own group
        if tag in (_W_PPR, _W_RPR):
            continue  # properties only; w:tab here is a tab-stop, not content
        if tag == _MC_FALLBACK and not include_fallback:
            continue
        if tag == _W_T:
            group.append((_TEXT, child, link))
        elif tag == _W_DELTEXT:
            group.append((_DELETED, child, link))
        elif tag == _W_TAB:
            group.append((_TAB, child, link))
        elif tag in (_W_BR, _W_CR):
            group.append((_BREAK, child, link))
        else:
            _collect_own_nodes(
                child,
                group,
                include_fallback=include_fallback,
                # Everything under a w:hyperlink is tagged with it, so the
                # rewrite can keep the link's text inside the link.
                link=child if tag == _W_HYPERLINK else link,
            )


def _visible_text(group: _Group) -> str:
    """The paragraph's visible text: ``w:t`` runs with tab/break separators."""
    chunks: list[str] = []
    for kind, element, _link in group:
        if kind == _TEXT:
            chunks.append(element.text or "")
        elif kind in _SEPARATOR_CHARS:
            chunks.append(_SEPARATOR_CHARS[kind])
    return "".join(chunks)


def _deleted_text(group: _Group) -> str:
    """The paragraph's tracked-deleted text (``w:delText`` nodes)."""
    return "".join(element.text or "" for kind, element, _link in group if kind == _DELETED)


# ---------------------------------------------------------------------------
# Direction-specific consumers of the walker
# ---------------------------------------------------------------------------


def _extract_from_root(root: Any, parts: list[str]) -> None:
    for group in _iter_paragraph_groups(root, include_fallback=False):
        visible = _visible_text(group).strip()
        if visible:
            parts.append(visible)
        deleted = _deleted_text(group).strip()
        if deleted:
            parts.append(deleted)


def _rewrite_root(root: Any, transform: Callable[[str], str]) -> bool:
    changed = False
    for group in _iter_paragraph_groups(root, include_fallback=True):
        if _rewrite_group(group, transform):
            changed = True
    return changed


def _link_segments(group: _Group) -> list[_Group]:
    """Split a paragraph group at ``w:hyperlink`` boundaries.

    Consecutive nodes outside any link form one segment; each hyperlink's own
    nodes form their own. Rewriting per segment keeps a replacement inside the
    link it came from (so a redacted address cannot survive in the link run)
    and keeps the link's text out of the surrounding runs.
    """
    segments: list[_Group] = []
    current: _Group = []
    current_link: Any = None
    for entry in group:
        if entry[2] is not current_link:
            if current:
                segments.append(current)
            current = []
            current_link = entry[2]
        current.append(entry)
    if current:
        segments.append(current)
    return segments


def _write_text_with_separators(element: Any, text: str) -> None:
    """Write *text* into the ``w:t`` *element*, re-materializing tabs and breaks.

    A literal ``\\t`` or ``\\n`` inside ``w:t`` is whitespace to Word, not a tab
    stop or a line break. python-docx's ``Run.text`` setter expands them into
    ``w:tab`` / ``w:br``, which is why the run-based rewrite this replaced kept
    them for free. Do the same, inserting the extra elements as siblings so the
    rest of the run — an inline ``w:drawing``, say — is untouched.
    """
    from docx.oxml.parser import OxmlElement

    pieces = _SEPARATOR_SPLIT.split(text)
    element.text = pieces[0]
    element.set(_XML_SPACE, "preserve")
    parent = element.getparent()
    if parent is None or len(pieces) == 1:
        return

    at = parent.index(element)
    for piece in pieces[1:]:
        tag = _SEPARATOR_TAGS.get(piece)
        if tag is not None:
            node = OxmlElement(tag)
        elif piece:
            node = OxmlElement("w:t")
            node.text = piece
            node.set(_XML_SPACE, "preserve")
        else:
            continue
        at += 1
        parent.insert(at, node)


def _rewrite_group(group: _Group, transform: Callable[[str], str]) -> bool:
    """Rewrite one paragraph's text nodes; return ``True`` if anything changed.

    The paragraph is rewritten one hyperlink segment at a time. Within a
    segment the transformed text lands in the first ``w:t`` (with tabs and
    breaks re-materialized as elements) and the remaining ``w:t`` are blanked.
    Non-text run content — ``w:drawing`` inline pictures in particular — is
    left untouched. ``w:delText`` nodes are transformed individually, never
    merged into visible text.
    """
    changed = False
    for segment in _link_segments(group):
        if _rewrite_segment(segment, transform):
            changed = True

    for kind, element, _link in group:
        if kind == _DELETED and element.text:
            new_deleted = transform(element.text)
            if new_deleted != element.text:
                element.text = new_deleted
                element.set(_XML_SPACE, "preserve")
                changed = True
    return changed


def _rewrite_segment(segment: _Group, transform: Callable[[str], str]) -> bool:
    text_nodes = [element for kind, element, _link in segment if kind == _TEXT]
    visible = _visible_text(segment)
    new_text = transform(visible)
    if new_text == visible or not text_nodes:
        return False

    # Old separator elements are dropped; _write_text_with_separators puts the
    # ones the rewritten text still calls for back next to the first w:t.
    for kind, element, _link in segment:
        if kind in _SEPARATOR_CHARS:
            parent = element.getparent()
            if parent is not None:
                parent.remove(element)
    _write_text_with_separators(text_nodes[0], new_text)
    for element in text_nodes[1:]:
        element.text = ""
    return True


def _strip_tracked_deletions(root: Any) -> bool:
    """Remove tracked-deletion content under *root*; return ``True`` if changed.

    Every ``w:del`` element is removed outright (deleted text is still in
    the file — a redacted document must not carry it). Any ``w:delText``
    outside a ``w:del`` wrapper (e.g. inside ``w:moveFrom``) is blanked.
    """
    changed = False
    for element in list(root.iter(_W_DEL)):
        parent = element.getparent()
        if parent is None:  # pragma: no cover - w:del is never a root
            continue
        parent.remove(element)
        changed = True
    for element in list(root.iter(_W_DELTEXT)):
        if element.text:
            element.text = ""
            changed = True
    return changed
