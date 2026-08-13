"""
app/config/__init__.py
-----------------------
Re-exports Settings and get_settings from core/config so that
either `from app.config.settings import get_settings` or
`from app.core.config import get_settings` works identically.
"""

from app.config.settings import Settings, get_settings  # noqa: F401

__all__ = ["Settings", "get_settings"]
