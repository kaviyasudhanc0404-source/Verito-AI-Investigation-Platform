"""
utils/file_manager.py
----------------------
File system helpers for the Evidence Collector Agent.

This module wraps the core file operations from file_utils.py and adds
additional helpers specific to evidence file management.

NOTE: This does NOT replace or delete file_utils.py — that module is
still used by evidence_service.py and other parts of the codebase.
file_manager.py is the new canonical entry point for agent code.
"""

from pathlib import Path

from app.utils.file_utils import (
    save_upload as _save_upload,
    read_file as _read_file,
    get_upload_dir as _get_upload_dir,
)


def save_file(
    file_bytes: bytes,
    filename: str,
    user_id: str,
    investigation_id: str,
) -> str:
    """
    Save uploaded file bytes to disk under the user/investigation directory.

    Args:
        file_bytes: Raw file bytes to write.
        filename: Original filename.
        user_id: Owning user ID (used to organise directory).
        investigation_id: Parent investigation ID.

    Returns:
        Relative file path (POSIX format) from the backend root.
    """
    return _save_upload(file_bytes, filename, user_id, investigation_id)


def read_file(file_path: str) -> bytes:
    """
    Read and return file bytes from a stored path.

    Args:
        file_path: Relative or absolute path to the file.

    Returns:
        Raw file bytes.

    Raises:
        FileNotFoundError: If the file does not exist at the given path.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Evidence file not found: {file_path}")
    return _read_file(file_path)


def file_exists(file_path: str) -> bool:
    """Check if a file exists at the given path."""
    return Path(file_path).exists()


def get_file_size(file_path: str) -> int:
    """Return the size in bytes of a file, or 0 if it does not exist."""
    path = Path(file_path)
    return path.stat().st_size if path.exists() else 0


def delete_file(file_path: str) -> bool:
    """
    Delete a file from disk.

    Args:
        file_path: Path to the file to delete.

    Returns:
        True if deleted, False if file did not exist.
    """
    path = Path(file_path)
    if path.exists():
        path.unlink()
        return True
    return False


def get_investigation_upload_dir(user_id: str, investigation_id: str) -> Path:
    """
    Return the upload directory Path for a user/investigation combination.
    Creates the directory if it does not exist.
    """
    return _get_upload_dir(user_id, investigation_id)
