"""
parsers/zip_extractor.py
-------------------------
Extract and parse every supported file inside a ZIP archive (in memory).

Members are parsed with the shared dispatch in app.parsers. Nested archives are
followed up to _MAX_DEPTH, and size/count limits guard against zip bombs.
Audio/video members are listed but not transcribed (they need a file on disk).
"""

from __future__ import annotations

import io
import zipfile

_MAX_DEPTH = 2
_MAX_MEMBERS = 500
_MAX_TOTAL_BYTES = 200 * 1024 * 1024


def parse(file_bytes: bytes, filename: str = "archive.zip", _depth: int = 0) -> str:
    """Parse every member. Raises ValueError for an invalid or oversized archive; problems with
    individual members become parse warnings so nothing is skipped silently."""
    from app.parsers import parse_bytes, parse_warning
    from app.utils.file_detector import detect_file_type

    try:
        archive = zipfile.ZipFile(io.BytesIO(file_bytes))
    except zipfile.BadZipFile as exc:
        raise ValueError(f"not a valid ZIP archive ({exc})") from exc

    with archive:
        all_members = [m for m in archive.infolist() if not m.is_dir()]
        members = all_members[:_MAX_MEMBERS]
        if not members:
            return ""
        if sum(m.file_size for m in members) > _MAX_TOTAL_BYTES:
            raise ValueError("the archive's uncompressed size exceeds the safety limit")

        lines = [f"[ZIP Archive Evidence: {filename}]", f"Contains {len(all_members)} file(s):"]
        lines += [f"  - {m.filename} ({m.file_size:,} bytes)" for m in members]
        if len(all_members) > _MAX_MEMBERS:
            lines.append(parse_warning(f"only the first {_MAX_MEMBERS} of {len(all_members)} files were read"))

        for member in members:
            file_type = detect_file_type(member.filename)
            lines.append(f"\n[Archive Member: {member.filename}]")
            if file_type == "zip" and _depth >= _MAX_DEPTH:
                lines.append(parse_warning(f"nested archive {member.filename} skipped (maximum depth reached)"))
                continue
            try:
                inner = archive.read(member.filename)
                text = parse(inner, member.filename, _depth + 1) if file_type == "zip" \
                    else parse_bytes(inner, member.filename, file_type)
            except Exception as exc:
                lines.append(parse_warning(f"{member.filename} could not be read ({exc})"))
                continue
            lines.append(text.strip() or parse_warning(f"no text extracted from {member.filename} ({file_type})"))

    return "\n".join(lines).strip()
