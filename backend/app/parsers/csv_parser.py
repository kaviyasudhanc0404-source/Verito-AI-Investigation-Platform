"""
parsers/csv_parser.py
----------------------
Render CSV evidence as labelled record lines (Python stdlib csv).

    [CSV Evidence: file.csv]
    Columns: A | B
    Total rows: N
    [Row 1] A: value | B: value
"""

import csv
import io

from app.parsers import decode_text, render_table


def parse(file_bytes: bytes, filename: str = "data.csv") -> str:
    rows = [row for row in csv.reader(io.StringIO(decode_text(file_bytes))) if any(c.strip() for c in row)]
    if not rows:
        return ""                                     # reported as an empty file by the collector
    header = [cell.strip() or f"Column {i + 1}" for i, cell in enumerate(rows[0])]
    return "\n".join([f"[CSV Evidence: {filename}]", *render_table(header, rows[1:])])
