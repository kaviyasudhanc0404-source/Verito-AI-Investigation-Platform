"""
utils/datetime_utils.py
------------------------
UTC datetime helpers used across the backend.
"""

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(tz=timezone.utc)
