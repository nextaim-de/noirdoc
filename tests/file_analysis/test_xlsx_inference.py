"""Fast characterization tests for ``pseudonymize_xlsx_smart`` (no ML models).

The SDK-level XLSX tests in ``tests/test_sdk_xlsx.py`` are ``slow`` and excluded from
CI, so the three-tier column pipeline had no fast coverage. These pin its behaviour
so the part-level work in ``xlsx_parts`` can refactor Tier 2 safely.
"""

from __future__ import annotations

import io

from openpyxl import Workbook, load_workbook

from noirdoc.file_analysis.xlsx_inference import pseudonymize_xlsx_smart
from noirdoc.pseudonymization.mapper import PseudonymMapper
from tests.file_analysis.xlsx_helpers import SubstringDetector, workbook_bytes


def _sheet_bytes(header: list[str], rows: list[list[object]]) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Daten"
    ws.append(header)
    for row in rows:
        ws.append(row)
    return workbook_bytes(wb)


async def test_header_tier1_pseudonymizes_classified_columns():
    data = _sheet_bytes(
        ["Name", "Email", "Notes"],
        [
            ["Anna Mueller", "anna@example.com", "leave alone"],
            ["Ben Schulz", "ben@example.com", "x"],
        ],
    )
    mapper = PseudonymMapper()

    result = await pseudonymize_xlsx_smart(data, SubstringDetector({}), mapper, language="de")

    assert result.new_bytes is not None
    assert result.entity_count == 4
    assert result.entity_types == {"PERSON": 2, "EMAIL": 2}
    assert result.column_classifications == {"Name": "PERSON (header)", "Email": "EMAIL (header)"}
    ws = load_workbook(io.BytesIO(result.new_bytes))["Daten"]
    assert ws.cell(2, 1).value.startswith("<<PERSON_")
    assert ws.cell(3, 1).value.startswith("<<PERSON_")
    assert ws.cell(2, 2).value.startswith("<<EMAIL_")
    assert ws.cell(3, 2).value.startswith("<<EMAIL_")
    assert ws.cell(2, 3).value == "leave alone"
    assert ws.cell(1, 1).value == "Name"


async def test_sample_tier2_classifies_unlabeled_column():
    names = [
        "Anna Mueller",
        "Ben Schulz",
        "Cara Weiss",
        "Dora Klein",
        "Emil Roth",
        "Finn Lang",
        "Gerd Haas",
    ]
    data = _sheet_bytes(["Spalte A"], [[n] for n in names])
    # Only the first name is "detectable" — it sits inside the 5-row sample window, which
    # must classify the whole column so rows beyond the sample get replaced too.
    detector = SubstringDetector({"Anna Mueller": "PERSON"})
    mapper = PseudonymMapper()

    result = await pseudonymize_xlsx_smart(data, detector, mapper, language="de")

    assert result.column_classifications == {"Spalte A": "PERSON (sampled)"}
    assert result.entity_count == len(names)
    assert result.new_bytes is not None
    ws = load_workbook(io.BytesIO(result.new_bytes))["Daten"]
    for row in range(2, 2 + len(names)):
        assert ws.cell(row, 1).value.startswith("<<PERSON_"), row


async def test_count_only_mode_does_not_touch_mapper():
    data = _sheet_bytes(["Name"], [["Anna Mueller"], ["Ben Schulz"]])
    mapper = PseudonymMapper()

    result = await pseudonymize_xlsx_smart(
        data, SubstringDetector({}), mapper, language="de", pseudonymize=False
    )

    assert result.new_bytes is None
    assert result.entity_count == 2
    assert mapper.entity_count == 0


async def test_no_pii_returns_no_bytes():
    data = _sheet_bytes(["Notes"], [["leave alone"], ["also untouched"]])
    mapper = PseudonymMapper()

    result = await pseudonymize_xlsx_smart(data, SubstringDetector({}), mapper, language="de")

    assert result.new_bytes is None
    assert result.entity_count == 0
    assert mapper.entity_count == 0
