"""
parsers/xml_parser.py
----------------------
Extract structured text from XML evidence files (.xml) using lxml / ElementTree.

Strategy:
  - Walk the entire element tree
  - Emit element tag + text content as labelled lines
  - Preserve tag names as context labels (e.g., "invoice > total: 5000")
  - Attribute values are included where present

Output format:
  [XML Evidence: filename.xml]
  root > child_tag: text value
  root > child_tag [@attr="value"]: text value
  ...

Falls back to xml.etree.ElementTree if lxml is not installed.
"""

from __future__ import annotations


def _iter_elements(element, path: str = "") -> list[str]:
    """
    Recursively walk XML elements and produce label: value lines.

    Args:
        element: An XML Element object.
        path:    Accumulated tag path for context.

    Returns:
        List of formatted strings representing element content.
    """
    lines: list[str] = []

    # Build current path
    tag = element.tag
    # Strip namespace if present: {http://...}TagName → TagName
    if "}" in tag:
        tag = tag.split("}", 1)[1]
    current_path = f"{path} > {tag}" if path else tag

    # Include attributes as context in path
    if element.attrib:
        attr_str = " ".join(f'@{k}="{v}"' for k, v in element.attrib.items())
        current_path_with_attrs = f"{current_path} [{attr_str}]"
    else:
        current_path_with_attrs = current_path

    # Emit text content if meaningful
    text = (element.text or "").strip()
    if text:
        lines.append(f"{current_path_with_attrs}: {text}")
    elif element.attrib:
        # No direct text but has attributes (e.g. <suspect name="John Doe">)
        # Emit the element line so attribute values appear in output
        lines.append(current_path_with_attrs)

    # Also emit each attribute as a searchable key: value line
    for attr_key, attr_val in element.attrib.items():
        if attr_val.strip():
            lines.append(f"{current_path} @{attr_key}: {attr_val}")

    # Recurse into children
    for child in element:
        lines.extend(_iter_elements(child, current_path))

    # Emit tail text (text after closing tag, before next sibling)
    tail = (element.tail or "").strip()
    if tail:
        lines.append(f"{path}: {tail}")

    return lines


def parse(file_bytes: bytes, filename: str = "data.xml") -> str:
    """
    Parse an XML file into human-readable evidence text.

    Preserves the hierarchical tag structure as readable label paths.

    Args:
        file_bytes: Raw .xml bytes.
        filename:   Original filename (for context header).

    Returns:
        Structured plain text with element paths and values.
    """
    # Try lxml first for better error handling; fall back to stdlib
    try:
        from lxml import etree as ET

        try:
            root = ET.fromstring(file_bytes)
        except ET.XMLSyntaxError as exc:
            return f"[XML Evidence: {filename}]\n(XML parse error: {exc})"

        # lxml Element is compatible with our walker
        lines = _iter_elements(root)

    except ImportError:
        # stdlib fallback
        import xml.etree.ElementTree as ET  # type: ignore

        try:
            root = ET.fromstring(file_bytes.decode("utf-8", errors="replace"))
        except ET.ParseError as exc:
            return f"[XML Evidence: {filename}]\n(XML parse error: {exc})"

        lines = _iter_elements(root)

    if not lines:
        return f"[XML Evidence: {filename}]\n(No text content found)"

    output_lines = [f"[XML Evidence: {filename}]", ""] + lines
    return "\n".join(output_lines).strip()
