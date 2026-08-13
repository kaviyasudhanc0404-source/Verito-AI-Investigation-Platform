"""
parsers/csv_parser.py
----------------------
Extract structured text from CSV evidence files.

Uses Python's built-in `csv` stdlib — no extra dependencies.

Output format:
  [Row 1]  Header1: Value1 | Header2: Value2 | ...
  [Row 2]  Header1: Value1 | Header2: Value2 | ...

Each row is rendered as key-value pairs using the header row as labels.
If no header is detected, columns are labelled Col_1, Col_2, etc.
"""

import csv
import io


def parse(file_bytes: bytes, filename: str = "data.csv") -> str:
    """
    Parse a CSV file into human-readable evidence text.

    Args:
        file_bytes: Raw CSV bytes.
        filename: Original filename (for context header).

    Returns:
        Structured plain text with labelled rows.
    """
    # Decode — try UTF-8-with-BOM first (handles Excel exports), fall back to latin-1
    try:
        text = file_bytes.decode("utf-8-sig")  # utf-8-sig strips BOM automatically
    except UnicodeDecodeError:
        text = file_bytes.decode("latin-1", errors="replace")
    # Extra safety: strip any residual BOM character
    text = text.lstrip("\ufeff")

    reader = csv.reader(io.StringIO(text))
    rows = list(reader)

    if not rows:
        return f"[CSV Evidence: {filename}]\n(Empty file)"

    # Filter completely empty rows
    rows = [r for r in rows if any(cell.strip() for cell in r)]
    if not rows:
        return f"[CSV Evidence: {filename}]\n(No data rows)"

    # Use first row as header
    header = [cell.strip() or f"Col_{i+1}" for i, cell in enumerate(rows[0])]
    data_rows = rows[1:]

    output_lines: list[str] = [
        f"[CSV Evidence: {filename}]",
        f"Columns: {' | '.join(header)}",
        f"Total rows: {len(data_rows)}",
        "",
    ]

    for row_num, row in enumerate(data_rows, start=1):
        # Pad short rows to header length
        padded = row + [""] * max(0, len(header) - len(row))
        pairs = " | ".join(
            f"{header[i]}: {padded[i].strip()}"
            for i in range(len(header))
            if padded[i].strip()  # skip empty cells
        )
        if pairs:
            output_lines.append(f"[Row {row_num}] {pairs}")

    return "\n".join(output_lines)
