"""
app/config/settings.py
-----------------------
Re-exports Settings and get_settings from app.core.config.
This module exists so the target file architecture is satisfied:
  from app.config.settings import get_settings
is equivalent to:
  from app.core.config import get_settings
"""

from app.core.config import Settings, get_settings  # noqa: F401

__all__ = ["Settings", "get_settings"]
