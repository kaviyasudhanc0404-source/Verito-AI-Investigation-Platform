"""
utils/file_utils.py
-------------------
File system helpers for evidence storage.
"""

import os
from pathlib import Path

from app.core.config import get_settings

settings = get_settings()


def get_upload_dir(user_id: str, investigation_id: str) -> Path:
    """
    Return the directory path for a specific user + investigation.
    Creates the directory if it does not exist.
    """
    path = Path(settings.upload_dir) / user_id / investigation_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def save_upload(
    file_bytes: bytes,
    filename: str,
    user_id: str,
    investigation_id: str,
) -> str:
    """
    Save uploaded file bytes to disk.
    Returns the relative file path (from the backend root).
    """
    directory = get_upload_dir(user_id, investigation_id)
    file_path = directory / filename
    file_path.write_bytes(file_bytes)
    # Store as a relative posix path
    return str(file_path.as_posix())


def read_file(file_path: str) -> bytes:
    """Read and return file bytes from a stored path."""
    return Path(file_path).read_bytes()


def detect_kind(filename: str) -> str:
    """Detect the evidence kind from the file extension."""
    ext = Path(filename).suffix.lower()
    image_exts = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".heic"}
    audio_exts = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}
    video_exts = {".mp4", ".mov", ".avi", ".mkv"}
    if ext in image_exts:
        return "image"
    if ext in audio_exts:
        return "audio"
    if ext in video_exts:
        return "video"
    if ext in {".eml"}:
        return "chat"
    return "doc"
