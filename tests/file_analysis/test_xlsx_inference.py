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
from tests.file_analysis.xlsx_helpers import (
    SubstringDetector,
    assert_no_part_contains,
    inject_app_props,
    inject_pivot,
    inject_threaded_comment_parts,
    part_names,
    workbook_bytes,
)


def _sheet_bytes(
    header: list[str], rows: list[list[object]], *, creator: str | None = None
) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Daten"
    ws.append(header)
    for row in rows:
        ws.append(row)
    # openpyxl defaults creator to "openpyxl" — a PERSON hit by design (no allowlist) — and
    # its *reader* substitutes that default again when the element is absent. An empty
    # element round-trips as None, so that is how a fixture gets "no creator".
    wb.properties.creator = creator if creator is not None else ""
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


# ── parts outside the cell grid (xlsx_parts wiring) ──────


async def test_metadata_only_workbook_is_rewritten():
    """Regression: a workbook whose only PII is metadata used to pass through byte-identical."""
    data = _sheet_bytes(["Notes"], [["leave alone"]], creator="Anna Mueller")
    mapper = PseudonymMapper()

    result = await pseudonymize_xlsx_smart(data, SubstringDetector({}), mapper, language="de")

    assert result.new_bytes is not None
    assert result.entity_count == 1
    assert result.entity_types == {"PERSON": 1}
    assert result.column_classifications["core.creator"] == "PERSON (forced)"
    assert load_workbook(io.BytesIO(result.new_bytes)).properties.creator == "<<PERSON_1>>"
    assert_no_part_contains(result.new_bytes, ["Anna Mueller"])


async def test_count_only_mode_counts_metadata_without_mutating():
    data = _sheet_bytes(["Notes"], [["leave alone"]], creator="Anna Mueller")
    mapper = PseudonymMapper()

    result = await pseudonymize_xlsx_smart(
        data, SubstringDetector({}), mapper, language="de", pseudonymize=False
    )

    assert result.new_bytes is None
    assert result.entity_count == 1
    assert mapper.entity_count == 0


async def test_unsupported_parts_are_dropped_and_counted():
    """app.xml Manager/Company and xl/persons are dropped by the writer — but only if a
    save happens, so they must count as hits even though nothing gets mapped."""
    data = _sheet_bytes(["Notes"], [["leave alone"]])
    data = inject_app_props(data, manager="Dora Klein", company="Schmidt GmbH")
    data = inject_threaded_comment_parts(data, display_name="Emil Roth", text="bitte prüfen")
    mapper = PseudonymMapper()

    result = await pseudonymize_xlsx_smart(data, SubstringDetector({}), mapper, language="de")

    assert result.new_bytes is not None
    names = part_names(result.new_bytes)
    assert not any(n.startswith(("xl/threadedComments/", "xl/persons/")) for n in names)
    assert_no_part_contains(result.new_bytes, ["Dora Klein", "Schmidt GmbH", "Emil Roth"])
    assert result.entity_types == {"PERSON": 2, "ORGANIZATION": 1}
    assert result.column_classifications["app.Manager"] == "PERSON (dropped)"
    assert result.column_classifications["app.Company"] == "ORGANIZATION (dropped)"
    assert result.column_classifications["persons.Emil Roth"] == "PERSON (dropped)"
    assert mapper.entity_count == 0  # dropped, not mapped — nothing to reveal


async def test_pivot_cache_matches_pseudonymized_cells():
    """The leger leak: sheet cells got placeholders while the pivot cache kept the originals."""
    data = _sheet_bytes(["Name", "Betrag"], [["Anna Mueller", 10], ["Ben Schulz", 20]])
    data = inject_pivot(
        data,
        refreshed_by="Dora Klein",
        fields=[("Name", ["Anna Mueller", "Ben Schulz"]), ("Betrag", None)],
        records=[[("x", 0), ("n", 10)], [("x", 1), ("n", 20)]],
    )
    mapper = PseudonymMapper()

    result = await pseudonymize_xlsx_smart(data, SubstringDetector({}), mapper, language="de")

    assert result.new_bytes is not None
    assert_no_part_contains(result.new_bytes, ["Anna Mueller", "Ben Schulz", "Dora Klein"])
    ws = load_workbook(io.BytesIO(result.new_bytes))["Daten"]
    cache = ws._pivots[0].cache
    shared = [item.v for item in cache.cacheFields[0].sharedItems._fields]
    assert shared == [ws["A2"].value, ws["A3"].value]
    assert result.entity_types == {"PERSON": 5}  # 2 cells + refreshedBy + 2 shared items
