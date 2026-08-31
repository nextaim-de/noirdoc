"""XLSX parts outside the cell grid must be pseudonymized and revealed.

Covers ``docProps/core.xml``, ``docProps/custom.xml``, cell comments, sheet
headers/footers and pivot caches — every surface that a plain openpyxl
round-trip preserves verbatim (and that a redacted workbook therefore leaked).
"""

from __future__ import annotations

import datetime

from openpyxl import Workbook
from openpyxl.comments import Comment

from noirdoc.file_analysis.xlsx_parts import pseudonymize_workbook_parts
from noirdoc.pseudonymization.mapper import PseudonymMapper
from tests.file_analysis.xlsx_helpers import (
    SubstringDetector,
    assert_no_part_contains,
    part_names,
    workbook_bytes,
)


def _workbook() -> Workbook:
    wb = Workbook()
    ws = wb.active
    ws.title = "Daten"
    ws.append(["Name", "Betrag"])
    ws.append(["Anna Mueller", 10])
    # openpyxl defaults creator to "openpyxl"; set it explicitly so counts are intentional.
    wb.properties.creator = "Anna Mueller"
    wb.properties.lastModifiedBy = "Dora Klein"
    return wb


# ── docProps/core.xml ────────────────────────────────────


async def test_core_author_fields_forced_to_person():
    wb = _workbook()
    mapper = PseudonymMapper()

    result = await pseudonymize_workbook_parts(wb, SubstringDetector({}), mapper, "de")

    assert wb.properties.creator == "<<PERSON_1>>"
    assert wb.properties.lastModifiedBy == "<<PERSON_2>>"
    assert result.entity_count == 2
    assert result.entity_types == {"PERSON": 2}
    assert result.classifications["core.creator"] == "PERSON (forced)"
    assert result.classifications["core.lastModifiedBy"] == "PERSON (forced)"
    assert mapper.reverse_lookup("<<PERSON_1>>") == "Anna Mueller"


async def test_core_author_field_with_email_is_labelled_email():
    wb = _workbook()
    wb.properties.lastModifiedBy = "dora.klein@example.com"

    await pseudonymize_workbook_parts(wb, SubstringDetector({}), PseudonymMapper(), "de")

    assert wb.properties.lastModifiedBy == "<<EMAIL_1>>"


async def test_core_author_shares_placeholder_with_matching_cell_value():
    wb = _workbook()
    mapper = PseudonymMapper()
    cell_placeholder = mapper.get_or_create("Anna Mueller", "PERSON")

    await pseudonymize_workbook_parts(wb, SubstringDetector({}), mapper, "de")

    assert wb.properties.creator == cell_placeholder


async def test_core_free_text_fields_go_through_detector():
    wb = _workbook()
    wb.properties.title = "Kundenliste Anna Mueller"
    wb.properties.subject = "Mandant Schmidt GmbH"
    wb.properties.description = "Kontakt: anna@example.com"
    wb.properties.keywords = "Anna Mueller, Bestand"
    wb.properties.category = "Kanzlei"
    detector = SubstringDetector(
        {"Anna Mueller": "PERSON", "Schmidt GmbH": "ORGANIZATION", "anna@example.com": "EMAIL"}
    )
    mapper = PseudonymMapper()

    result = await pseudonymize_workbook_parts(wb, detector, mapper, "de")

    assert wb.properties.title == "Kundenliste <<PERSON_1>>"
    assert wb.properties.subject == "Mandant <<ORGANIZATION_1>>"
    assert wb.properties.description == "Kontakt: <<EMAIL_1>>"
    assert wb.properties.keywords == "<<PERSON_1>>, Bestand"
    assert wb.properties.category == "Kanzlei"
    # creator + lastModifiedBy (forced) + title + subject + description + keywords
    assert result.entity_types == {"PERSON": 4, "ORGANIZATION": 1, "EMAIL": 1}
    assert result.classifications["core.title"] == "PERSON (detected)"


async def test_core_none_and_blank_fields_are_skipped():
    wb = _workbook()
    wb.properties.creator = None
    wb.properties.lastModifiedBy = "   "
    wb.properties.title = None

    result = await pseudonymize_workbook_parts(wb, SubstringDetector({}), PseudonymMapper(), "de")

    assert result.entity_count == 0
    assert wb.properties.creator is None
    assert wb.properties.lastModifiedBy == "   "


async def test_generic_creator_is_still_mapped_no_allowlist():
    wb = _workbook()
    wb.properties.creator = "Microsoft Office User"
    wb.properties.lastModifiedBy = "openpyxl"

    await pseudonymize_workbook_parts(wb, SubstringDetector({}), PseudonymMapper(), "de")

    assert wb.properties.creator == "<<PERSON_1>>"
    assert wb.properties.lastModifiedBy == "<<PERSON_2>>"


# ── docProps/custom.xml ──────────────────────────────────


async def test_custom_string_props_pseudonymized_other_types_untouched():
    from openpyxl.packaging.custom import (
        BoolProperty,
        DateTimeProperty,
        FloatProperty,
        IntProperty,
        LinkProperty,
        StringProperty,
    )

    wb = _workbook()
    seit = datetime.datetime(2024, 1, 1)
    for prop in (
        StringProperty(name="Mandant", value="Akte Schmidt GmbH"),
        StringProperty(name="Leer", value=None),
        IntProperty(name="Akte", value=42),
        FloatProperty(name="Quote", value=0.5),
        BoolProperty(name="Aktiv", value=True),
        DateTimeProperty(name="Seit", value=seit),
        LinkProperty(name="Link", value="Daten!A1"),
    ):
        wb.custom_doc_props.append(prop)
    detector = SubstringDetector({"Schmidt GmbH": "ORGANIZATION"})

    result = await pseudonymize_workbook_parts(wb, detector, PseudonymMapper(), "de")

    props = wb.custom_doc_props
    assert props["Mandant"].value == "Akte <<ORGANIZATION_1>>"
    assert props["Leer"].value is None
    assert props["Akte"].value == 42
    assert props["Quote"].value == 0.5
    assert props["Aktiv"].value is True
    assert props["Seit"].value == seit
    assert props["Link"].value == "Daten!A1"
    assert result.classifications["custom.Mandant"] == "ORGANIZATION (detected)"
    assert result.entity_types == {"PERSON": 2, "ORGANIZATION": 1}


# ── cell comments ────────────────────────────────────────


async def test_comment_author_forced_and_text_detected():
    wb = _workbook()
    ws = wb["Daten"]
    ws["A2"].comment = Comment("Kunde anna@example.com", "Dora Klein")
    detector = SubstringDetector({"anna@example.com": "EMAIL"})
    mapper = PseudonymMapper()

    result = await pseudonymize_workbook_parts(wb, detector, mapper, "de")

    comment = ws["A2"].comment
    assert comment.author == "<<PERSON_2>>"  # same placeholder as lastModifiedBy "Dora Klein"
    assert comment.text == "Kunde <<EMAIL_1>>"
    assert result.classifications["comment.author@Daten!A2"] == "PERSON (forced)"
    assert result.classifications["comment.text@Daten!A2"] == "EMAIL (detected)"
    assert result.entity_types == {"PERSON": 3, "EMAIL": 1}


async def test_comment_author_prefix_in_text_replaced_without_detector():
    wb = _workbook()
    ws = wb["Daten"]
    # Excel's legacy comment convention: the body starts with "Author:" — NER is not
    # reliable on a bare name followed by a colon, so this must be deterministic.
    ws["A2"].comment = Comment("Dora Klein:\nbitte prüfen", "Dora Klein")

    result = await pseudonymize_workbook_parts(wb, SubstringDetector({}), PseudonymMapper(), "de")

    assert ws["A2"].comment.text == "<<PERSON_2>>:\nbitte prüfen"
    assert result.entity_types == {"PERSON": 4}


async def test_empty_comment_is_tolerated():
    wb = _workbook()
    wb["Daten"]["A2"].comment = Comment("", "")

    result = await pseudonymize_workbook_parts(wb, SubstringDetector({}), PseudonymMapper(), "de")

    assert result.entity_count == 2  # only creator + lastModifiedBy


async def test_comment_parts_hold_no_original_after_save():
    wb = _workbook()
    ws = wb["Daten"]
    ws["A2"].comment = Comment("Dora Klein:\nKontakt anna@example.com", "Dora Klein")
    ws["A2"].value = "<<PERSON_1>>"  # the cell grid is not this module's job
    detector = SubstringDetector({"anna@example.com": "EMAIL"})

    await pseudonymize_workbook_parts(wb, detector, PseudonymMapper(), "de")

    out = workbook_bytes(wb)
    assert any("comments" in n for n in part_names(out))
    assert any(n.endswith(".vml") for n in part_names(out))
    assert_no_part_contains(out, ["Anna Mueller", "Dora Klein", "anna@example.com"])


# ── sheet headers / footers ──────────────────────────────


async def test_header_footer_parts_pseudonymized():
    wb = _workbook()
    ws = wb["Daten"]
    ws.oddHeader.left.text = "Erstellt von Anna Mueller"
    ws.oddFooter.center.text = "Kontakt anna@example.com"
    ws.evenHeader.right.text = "Anna Mueller"
    ws.evenFooter.left.text = "Seite &P"
    ws.firstHeader.center.text = "Anna Mueller"
    ws.firstFooter.right.text = "Anna Mueller"
    detector = SubstringDetector({"Anna Mueller": "PERSON", "anna@example.com": "EMAIL"})

    result = await pseudonymize_workbook_parts(wb, detector, PseudonymMapper(), "de")

    assert ws.oddHeader.left.text == "Erstellt von <<PERSON_1>>"
    assert ws.oddFooter.center.text == "Kontakt <<EMAIL_1>>"
    assert ws.evenHeader.right.text == "<<PERSON_1>>"
    assert ws.evenFooter.left.text == "Seite &P"
    assert ws.firstHeader.center.text == "<<PERSON_1>>"
    assert ws.firstFooter.right.text == "<<PERSON_1>>"
    assert result.classifications["header.odd.left@Daten"] == "PERSON (detected)"
    assert result.classifications["footer.odd.center@Daten"] == "EMAIL (detected)"
    assert result.entity_types == {"PERSON": 6, "EMAIL": 1}
    ws["A2"].value = "<<PERSON_1>>"
    assert_no_part_contains(workbook_bytes(wb), ["Anna Mueller", "anna@example.com"])
