"""XLSX parts outside the cell grid must be pseudonymized and revealed.

Covers ``docProps/core.xml``, ``docProps/custom.xml``, cell comments, sheet
headers/footers and pivot caches — every surface that a plain openpyxl
round-trip preserves verbatim (and that a redacted workbook therefore leaked).
"""

from __future__ import annotations

from openpyxl import Workbook

from noirdoc.file_analysis.xlsx_parts import pseudonymize_workbook_parts
from noirdoc.pseudonymization.mapper import PseudonymMapper
from tests.file_analysis.xlsx_helpers import SubstringDetector


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
