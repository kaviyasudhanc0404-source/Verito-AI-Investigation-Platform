"""
utils/file_utils.py
-------------------
Evidence file storage on local disk.

Uploaded files are stored under UPLOAD_DIR/<user_id>/<investigation_id>/ with a
server-generated name. The client's filename is only kept as metadata, so it can
never escape the upload folder or overwrite another upload.
"""

import re
import uuid
from pathlib import Path

from app.core.config import get_settings

settings = get_settings()

_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_display_name(filename: str | None) -> str:
    """Strip any directory parts and unsafe characters from a client filename."""
    name = Path((filename or "").replace("\\", "/")).name
    name = _UNSAFE_CHARS.sub("_", name).strip(" .")
    return name[:180] or "unnamed_file"


def get_upload_dir(user_id: str, investigation_id: str) -> Path:
    """Return (and create) the upload folder for one investigation."""
    path = Path(settings.upload_dir) / user_id / investigation_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_upload(file_bytes: bytes, filename: str, user_id: str, investigation_id: str) -> str:
    """Write the upload under a unique name and return its relative posix path."""
    suffix = Path(filename).suffix.lower()
    file_path = get_upload_dir(user_id, investigation_id) / f"{uuid.uuid4().hex}{suffix}"
    file_path.write_bytes(file_bytes)
    return file_path.as_posix()
