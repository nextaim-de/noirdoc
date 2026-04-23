"""XLSX text extraction using openpyxl."""

from __future__ import annotations

import io


def extract_xlsx(data: bytes) -> str:
    """Extract cell values from all sheets of an XLSX byte-string."""
    from openpyxl import load_workbook

    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines: list[str] = []
    try:
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            for row in ws.iter_rows(values_only=True):
                row_text = " | ".join(str(v) for v in row if v is not None)
                if row_text.strip():
                    lines.append(row_text)
    finally:
        wb.close()
    return "\n".join(lines)
