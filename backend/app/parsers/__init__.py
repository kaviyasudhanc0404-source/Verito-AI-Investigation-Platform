"""
app/parsers/__init__.py
------------------------
Evidence file parsers package.

Docling handles all document types (PDF, DOCX, PPTX, HTML, Markdown)
through a single unified function — no separate wrapper files needed.

Parser → File Type Mapping:
  docling_parser  → .pdf .docx .pptx .html .htm .md  (Docling)
  image_parser    → .jpg .png .webp …                 (EasyOCR + Gemini)
  email_parser    → .eml                              (Python stdlib email)
  msg_parser      → .msg                              (extract-msg)
  csv_parser      → .csv                              (Python stdlib csv)
  excel_parser    → .xlsx / .xls                      (pandas + openpyxl)
  xml_parser      → .xml                              (lxml / ElementTree)
  chat_parser     → .txt / .json                      (custom)
  zip_extractor   → .zip                              (zipfile — recursive)
"""

from app.parsers.docling_parser import parse_with_docling
from app.parsers.image_parser import parse as parse_image
from app.parsers.email_parser import parse as parse_email
from app.parsers.msg_parser import parse as parse_msg
from app.parsers.csv_parser import parse as parse_csv
from app.parsers.excel_parser import parse as parse_excel
from app.parsers.xml_parser import parse as parse_xml
from app.parsers.chat_parser import parse as parse_chat
from app.parsers.zip_extractor import parse as parse_zip

__all__ = [
    "parse_with_docling",
    "parse_image",
    "parse_email",
    "parse_msg",
    "parse_csv",
    "parse_excel",
    "parse_xml",
    "parse_chat",
    "parse_zip",
]
