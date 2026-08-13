"""
parsers/excel_parser.py
------------------------
Extract structured text from Excel evidence files (.xlsx, .xls) using pandas.

Uses pandas with the openpyxl engine to:
  - Read all worksheets
  - Render each worksheet as labelled key-value rows (matching csv_parser style)
  - Preserve column headers and all cell values

Output format:
  [Excel Evidence: filename.xlsx]
  [Sheet: Sheet1]  Columns: Col1 | Col2 | ...  Total rows: N

  [Row 1]  Col1: Value1 | Col2: Value2 | ...
  [Row 2]  ...

  [Sheet: Sheet2]
  ...
"""

from __future__ import annotations


def parse(file_bytes: bytes, filename: str = "data.xlsx") -> str:
    """
    Parse an Excel file (.xlsx / .xls) into human-readable evidence text.

    All sheets are extracted. Each row is rendered as header: value pairs.

    Args:
        file_bytes: Raw .xlsx / .xls bytes.
        filename:   Original filename (for context header).

    Returns:
        Structured plain text with all sheet data.
    """
    try:
        import io
        import pandas as pd
    except ImportError as exc:
        raise ImportError(
            "pandas and openpyxl are required for Excel parsing. "
            "Run: pip install pandas openpyxl"
        ) from exc

    engine = "openpyxl" if filename.lower().endswith(".xlsx") else "xlrd"

    try:
        sheets: dict = pd.read_excel(
            io.BytesIO(file_bytes),
            sheet_name=None,   # load ALL sheets
            engine=engine,
            dtype=str,         # keep everything as string — preserves dates, codes, etc.
        )
    except Exception as exc:
        return f"[Excel Evidence: {filename}]\n(Failed to parse: {exc})"

    if not sheets:
        return f"[Excel Evidence: {filename}]\n(No sheets found)"

    output_lines: list[str] = [f"[Excel Evidence: {filename}]"]

    for sheet_name, df in sheets.items():
        # Drop completely empty rows and columns
        df = df.dropna(how="all").dropna(axis=1, how="all")

        if df.empty:
            output_lines.append(f"\n[Sheet: {sheet_name}]")
            output_lines.append("(Empty sheet)")
            continue

        # Clean header names
        headers = [str(col).strip() or f"Col_{i+1}" for i, col in enumerate(df.columns)]

        output_lines.append(f"\n[Sheet: {sheet_name}]")
        output_lines.append(f"Columns: {' | '.join(headers)}")
        output_lines.append(f"Total rows: {len(df)}")
        output_lines.append("")

        for row_num, (_, row) in enumerate(df.iterrows(), start=1):
            pairs = " | ".join(
                f"{headers[i]}: {str(val).strip()}"
                for i, val in enumerate(row)
                if str(val).strip() not in {"", "nan", "None"}
            )
            if pairs:
                output_lines.append(f"[Row {row_num}] {pairs}")

    return "\n".join(output_lines).strip()
