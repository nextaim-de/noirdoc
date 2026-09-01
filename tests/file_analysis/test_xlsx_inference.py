"""Fast characterization tests for ``pseudonymize_xlsx_smart`` (no ML models).

The SDK-level XLSX tests in ``tests/test_sdk_xlsx.py`` are ``slow`` and excluded from
CI, so the three-tier column pipeline had no fast coverage. These pin its behaviour
so the part-level work in ``xlsx_parts`` can refactor Tier 2 safely.
"""

from __future__ import annotations

import io

from openpyxl import Workbook, load_workbook

from noirdoc.file_analysis.xlsx_inference import pseudonymize_xlsx_smart
from noirdoc.file_reidentification.service import reidentify_file_bytes
from noirdoc.pseudonymization.mapper import PseudonymMapper
from tests.file_analysis.xlsx_helpers import (
    SubstringDetector,
    assert_no_part_contains,
    inject_app_props,
    inject_pivot,
    inject_threaded_comment_parts,
    part_names,
    strip_core_creator,
    workbook_bytes,
)

_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


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
    assert result.column_classifications["persons.1"] == "PERSON (dropped)"  # never the name
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


# ── review round 1 ───────────────────────────────────────


async def test_pivot_cache_scrubbed_when_only_the_sheet_sample_hits():
    """Column classified from row 2, but the cache lists that value beyond its sample window."""
    names = ["Anna Mueller", "Ben Schulz", "Cara Weiss", "Dora Klein", "Emil Roth", "Finn Lang"]
    data = _sheet_bytes(["Spalte1", "Betrag"], [[n, i] for i, n in enumerate(names)])
    data = inject_pivot(
        data,
        refreshed_by="Gerd Haas",
        fields=[("Spalte1", list(reversed(names))), ("Betrag", None)],
        records=[[("x", i), ("n", i)] for i in range(len(names))],
        source_ref="A1:B7",
    )
    detector = SubstringDetector({"Anna Mueller": "PERSON"})

    result = await pseudonymize_xlsx_smart(data, detector, PseudonymMapper(), language="de")

    assert result.column_classifications["Spalte1"] == "PERSON (sampled)"
    assert result.column_classifications["pivot1.Spalte1"] == "PERSON (sheet)"
    assert result.new_bytes is not None
    assert_no_part_contains(result.new_bytes, [*names, "Gerd Haas"])


async def test_absent_creator_element_is_not_a_phantom_person():
    """openpyxl's reader substitutes creator='openpyxl' when the element is missing."""
    data = strip_core_creator(_sheet_bytes(["Notes"], [["leave alone"]], creator="Anna Mueller"))
    mapper = PseudonymMapper()

    result = await pseudonymize_xlsx_smart(data, SubstringDetector({}), mapper, language="de")

    assert result.entity_count == 0
    assert result.new_bytes is None
    assert mapper.entity_count == 0


async def test_chartsheet_is_skipped_by_the_cell_pass_and_its_header_scrubbed():
    from openpyxl.chart import BarChart, Reference

    wb = Workbook()
    ws = wb.active
    ws.title = "Daten"
    ws.append(["Name", "Betrag"])
    ws.append(["Anna Mueller", 10])
    ws.append(["Ben Schulz", 20])
    wb.properties.creator = ""
    chartsheet = wb.create_chartsheet("Chart")
    chart = BarChart()
    chart.add_data(Reference(ws, min_col=2, min_row=1, max_row=3), titles_from_data=True)
    chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=3))
    chartsheet.add_chart(chart)
    chartsheet.oddHeader.left.text = "Erstellt von Anna Mueller"
    data = workbook_bytes(wb)
    assert "Chart" in load_workbook(io.BytesIO(data)).sheetnames, "fixture: chartsheet lost"
    detector = SubstringDetector({"Anna Mueller": "PERSON"})
    mapper = PseudonymMapper()

    result = await pseudonymize_xlsx_smart(data, detector, mapper, language="de")

    assert result.new_bytes is not None
    out = load_workbook(io.BytesIO(result.new_bytes))
    assert out["Daten"]["A2"].value.startswith("<<PERSON_")
    assert out["Chart"].oddHeader.left.text == "Erstellt von <<PERSON_1>>"

    revealed = reidentify_file_bytes(result.new_bytes, _XLSX_MIME, mapper.get_mapping_summary())
    assert revealed is not None
    back = load_workbook(io.BytesIO(revealed))
    assert back["Daten"]["A2"].value == "Anna Mueller"
    assert back["Chart"].oddHeader.left.text == "Erstellt von Anna Mueller"
