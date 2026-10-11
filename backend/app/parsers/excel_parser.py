"""
parsers/excel_parser.py
------------------------
Render every worksheet of an Excel file (.xlsx / .xls) as labelled record lines.

    [Excel Evidence: file.xlsx]

    [Sheet: Purchase Orders]
    Columns: PO Number | Date | …
    Total rows: N
    [Row 1] PO Number: PO-2026-0341 | Date: 2026-03-05 | …
"""

from __future__ import annotations

import io

from app.parsers import render_table


def parse(file_bytes: bytes, filename: str = "data.xlsx") -> str:
    import pandas as pd

    engine = "openpyxl" if filename.lower().endswith(".xlsx") else "xlrd"
    header = f"[Excel Evidence: {filename}]"
    try:
        sheets = pd.read_excel(io.BytesIO(file_bytes), sheet_name=None, engine=engine, dtype=str)
    except Exception as exc:
        return f"{header}\n(Failed to parse: {exc})"

    lines = [header]
    for sheet_name, frame in sheets.items():
        frame = frame.dropna(how="all").dropna(axis=1, how="all")
        lines += ["", f"[Sheet: {sheet_name}]"]
        if frame.empty:
            lines.append("(Empty sheet)")
            continue
        columns = [str(col).strip() or f"Column {i + 1}" for i, col in enumerate(frame.columns)]
        lines += render_table(columns, frame.values.tolist())
    return "\n".join(lines).strip()
