"""DOCX/XLSX reconstruction must replace exactly what pseudonymize replaced.

Reconstruction is a find-and-replace over the *file* (paragraph runs, cell
values) rather than over the extracted text, so it needs one thing the
extracted text cannot tell it: which placeholder stands for which original.
Deriving that from entity offsets is a second implementation of the builder's
overlap semantics, and it drifts — an overlapping entity contributes a
remainder pseudonym that entity-offset arithmetic cannot see, and every later
entity is then looked up at the wrong offset.

These tests pin the contract end to end: every placeholder in the
pseudonymized text is in the replacement map under the exact original it
replaced, and applying the map to the extracted text reproduces the
pseudonymized text byte for byte.
"""

from __future__ import annotations

import io
import random
import re
import string

from structlog.testing import capture_logs

from noirdoc.detection.base import DetectedEntity
from noirdoc.file_analysis.models import FileBlock
from noirdoc.file_analysis.reconstruction import (
    _build_replacements,
    _reconstruct_docx,
    _reconstruct_xlsx,
    pseudonymize_block,
)
from noirdoc.pseudonymization.mapper import PseudonymMapper

_DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_PLACEHOLDER = re.compile(r"<<[A-Z_]+_\d+>>")


def _ent(entity_type: str, text: str, start: int, end: int) -> DetectedEntity:
    return DetectedEntity(
        entity_type=entity_type,
        text=text,
        start=start,
        end=end,
        score=0.9,
        source="test",
    )


def _block(mime: str, content: bytes, text: str, entities: list[DetectedEntity]) -> FileBlock:
    """Build a block the way the pipeline does: extract, detect, pseudonymize."""
    block = FileBlock(
        content_bytes=content,
        mime_type=mime,
        source_path="test",
        source_type="file",
        extracted_text=text,
        entities=entities,
    )
    return block


def _docx_bytes(paragraph: str) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_paragraph(paragraph)
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _docx_text(data: bytes) -> str:
    from docx import Document

    doc = Document(io.BytesIO(data))
    return "\n".join(p.text for p in doc.paragraphs if p.text)


def _xlsx_bytes(cell_value: str) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    wb.active["A1"] = cell_value
    buf = io.BytesIO()
    wb.save(buf)
    wb.close()
    return buf.getvalue()


def _xlsx_cell(data: bytes) -> str:
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data))
    value = wb.active["A1"].value
    wb.close()
    return str(value)


def _apply(replacements: dict[str, str], text: str) -> str:
    """Apply the replacement map the way the DOCX/XLSX writers do."""
    for original, pseudonym in replacements.items():
        text = text.replace(original, pseudonym)
    return text


# --- The mixed document: one overlapping pair among plain entities ---------

_MIXED = "Anna Weber wohnt in Berlin und arbeitet bei Weber GmbH in Hamburg."


def _mixed_entities() -> list[DetectedEntity]:
    org = _MIXED.index("Weber GmbH")
    berlin = _MIXED.index("Berlin")
    hamburg = _MIXED.index("Hamburg")
    return [
        _ent("PERSON", "Anna Weber", 0, 10),
        _ent("LOCATION", "Berlin", berlin, berlin + 6),
        _ent("PERSON", "Weber", org, org + 5),
        _ent("ORGANIZATION", "Weber GmbH", org, org + 10),
        _ent("LOCATION", "Hamburg", hamburg, hamburg + 7),
    ]


def test_replacements_cover_every_placeholder_of_an_overlapping_document():
    """The remainder pseudonym must not knock the later entities off their offsets."""
    mapper = PseudonymMapper()
    block = _block(_DOCX_MIME, _docx_bytes(_MIXED), _MIXED, _mixed_entities())
    pseudonymize_block(block, mapper)

    replacements = _build_replacements(block)

    assert replacements == {
        "Anna Weber": "<<PERSON_2>>",
        "Hamburg": "<<LOCATION_1>>",
        "Berlin": "<<LOCATION_2>>",
        "Weber": "<<PERSON_1>>",
        "GmbH": "<<ORGANIZATION_1>>",
    }
    # Longest original first, so "Weber" cannot eat the "Weber" inside "Anna Weber".
    assert list(replacements) == sorted(replacements, key=len, reverse=True)
    assert _apply(replacements, _MIXED) == block.pseudonymized_text


def test_reconstruct_docx_matches_the_pseudonymized_text_byte_for_byte():
    mapper = PseudonymMapper()
    block = _block(_DOCX_MIME, _docx_bytes(_MIXED), _MIXED, _mixed_entities())
    pseudonymize_block(block, mapper)

    new_bytes = _reconstruct_docx(block)
    assert new_bytes is not None
    assert _docx_text(new_bytes) == block.pseudonymized_text
    assert "GmbH" not in _docx_text(new_bytes)
    assert "Hamburg" not in _docx_text(new_bytes)


def test_reconstruct_xlsx_matches_the_pseudonymized_text_byte_for_byte():
    mapper = PseudonymMapper()
    block = _block(_XLSX_MIME, _xlsx_bytes(_MIXED), _MIXED, _mixed_entities())
    pseudonymize_block(block, mapper)

    new_bytes = _reconstruct_xlsx(block)
    assert new_bytes is not None
    assert _xlsx_cell(new_bytes) == block.pseudonymized_text
    assert "GmbH" not in _xlsx_cell(new_bytes)
    assert "Hamburg" not in _xlsx_cell(new_bytes)


# --- The replacement map has to reproduce the masked text -----------------


def test_replacements_refused_when_an_original_hides_in_a_placeholder():
    """An original made of placeholder characters can corrupt a placeholder.

    Replacement rewrites the file by text, so an original that also occurs
    INSIDE an already-inserted placeholder gets rewritten there too. "12" is
    an ID here and also the counter of `<<PERSON_12>>`, which would come out
    as `<<PERSON_<<ID_1>>>>` — masked, but no longer revealable.

    Reconstruction refuses instead: the caller falls back to converted text,
    which keeps both the masking and the reveal and only loses the formatting.
    """
    people = [f"N{i:02d}" for i in range(12)]
    text = "code 12 dann " + " ".join(people)
    entities = [_ent("ID", "12", text.index("12"), text.index("12") + 2)]
    for name in people:
        start = text.index(name)
        entities.append(_ent("PERSON", name, start, start + len(name)))

    mapper = PseudonymMapper()
    block = _block(_DOCX_MIME, _docx_bytes(text), text, entities)
    pseudonymize_block(block, mapper)
    assert "<<PERSON_12>>" in (block.pseudonymized_text or "")

    with capture_logs() as logs:
        assert _build_replacements(block) is None
    assert [entry["event"] for entry in logs] == [
        "reconstruction.replacement_collides_with_placeholder"
    ]

    assert _reconstruct_docx(block) is None
    assert _reconstruct_xlsx(block) is None


def test_replacements_pass_the_self_check_on_a_normal_document():
    """The guard must not fire on ordinary text — reconstruction proceeds."""
    mapper = PseudonymMapper()
    block = _block(_DOCX_MIME, _docx_bytes(_MIXED), _MIXED, _mixed_entities())
    pseudonymize_block(block, mapper)

    with capture_logs() as logs:
        replacements = _build_replacements(block)
    assert replacements is not None
    assert logs == []
    assert _apply(replacements, _MIXED) == block.pseudonymized_text
    assert _reconstruct_docx(block) is not None


# --- No overlap: the behaviour that already worked must keep working -------

_PLAIN = "Anna Weber wohnt in Berlin und schreibt an max@test.de."


def test_reconstruct_without_overlaps_is_unchanged():
    """The pre-existing (non-overlapping) case: same replacements, same output."""
    mapper = PseudonymMapper()
    email = _PLAIN.index("max@test.de")
    berlin = _PLAIN.index("Berlin")
    entities = [
        _ent("PERSON", "Anna Weber", 0, 10),
        _ent("LOCATION", "Berlin", berlin, berlin + 6),
        _ent("EMAIL", "max@test.de", email, email + 11),
    ]
    block = _block(_DOCX_MIME, _docx_bytes(_PLAIN), _PLAIN, entities)
    pseudonymize_block(block, mapper)

    assert _build_replacements(block) == {
        "Anna Weber": "<<PERSON_1>>",
        "max@test.de": "<<EMAIL_1>>",
        "Berlin": "<<LOCATION_1>>",
    }
    new_bytes = _reconstruct_docx(block)
    assert new_bytes is not None
    assert _docx_text(new_bytes) == block.pseudonymized_text


# --- Randomized sweep over overlapping shapes -----------------------------
#
# Same alphabet discipline as the engine's property test: unique
# non-whitespace characters, disjoint from the placeholder charset, so every
# span text occurs exactly once and a replacement can be checked by substring.
# Entity edges are snapped off whitespace, the way real detector spans are.
#
# That disjointness also makes this sweep blind to originals drawn from the
# placeholder charset itself ("12" inside `<<PERSON_12>>`) — deliberately, so
# the invariants stay decidable. `..._an_original_hides_in_a_placeholder`
# above covers that class.

_UNIQUE_ALPHABET = string.ascii_lowercase + "!#$%&()*+,-./:;=?@[]^{|}~"
_TYPES = ("PERSON", "LOCATION", "EMAIL", "ORGANIZATION")


def _random_text(rng: random.Random) -> str:
    out: list[str] = []
    for char in _UNIQUE_ALPHABET[: rng.randrange(20, len(_UNIQUE_ALPHABET) + 1)]:
        out.append(char)
        if rng.random() < 0.2:
            out.append(" ")
    return "".join(out)


def _random_entities(rng: random.Random, text: str) -> list[DetectedEntity]:
    entities: list[DetectedEntity] = []
    for _ in range(rng.randrange(1, 9)):
        start = rng.randrange(len(text))
        end = min(len(text), start + rng.randrange(1, 12))
        # Detector spans never carry edge whitespace.
        while start < end and text[start].isspace():
            start += 1
        while end > start and text[end - 1].isspace():
            end -= 1
        if start == end:
            continue
        entities.append(_ent(rng.choice(_TYPES), text[start:end], start, end))
    rng.shuffle(entities)
    return entities


def test_replacement_sweep_over_overlapping_documents():
    """Zero missing and zero wrong replacements across 3 000 randomized cases."""
    rng = random.Random(20260814)
    missing = 0
    wrong = 0
    not_reproduced = 0
    overlapping_cases = 0

    for _ in range(3000):
        text = _random_text(rng)
        entities = _random_entities(rng, text)
        if not entities:
            continue

        mapper = PseudonymMapper()
        block = _block(_DOCX_MIME, b"", text, entities)
        pseudonymize_block(block, mapper)
        pseudonymized = block.pseudonymized_text or ""

        # Expected map, derived from the output alone: every placeholder in it,
        # under the original the mapper hands back for it.
        expected = {
            mapper.reverse_lookup(token): token for token in _PLACEHOLDER.findall(pseudonymized)
        }
        entity_texts = {e.text for e in entities}
        if any(span.original not in entity_texts for span in block.emitted_spans or []):
            overlapping_cases += 1

        replacements = _build_replacements(block) or {}
        for original, token in expected.items():
            assert original is not None
            if original not in replacements:
                missing += 1
            elif replacements[original] != token:
                wrong += 1

        if _apply(replacements, text) != pseudonymized:
            not_reproduced += 1

    assert (missing, wrong, not_reproduced) == (0, 0, 0)
    # Guard against a vacuous run: remainders must actually have occurred.
    assert overlapping_cases > 300
