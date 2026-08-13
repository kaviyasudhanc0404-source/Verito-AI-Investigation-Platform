"""
core/security.py
----------------
JWT creation/verification and Argon2 password hashing.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHashError

from app.core.config import get_settings

settings = get_settings()

# Argon2 hasher — uses secure defaults (time_cost=3, memory_cost=65536)
_hasher = PasswordHasher()


# ── Password helpers ──────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Hash a plain-text password using Argon2id."""
    return _hasher.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """
    Return True if plain matches the Argon2 hash.
    Returns False (never raises) on mismatch.
    """
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


# ── JWT helpers ───────────────────────────────────────────────────────────────

def create_access_token(payload: dict[str, Any]) -> str:
    """
    Create a signed JWT.
    Adds `iat` (issued-at) and `exp` (expiry) claims automatically.
    """
    now = datetime.now(tz=timezone.utc)
    data = {
        **payload,
        "iat": now,
        "exp": now + timedelta(minutes=settings.jwt_expire_minutes),
    }
    return jwt.encode(data, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict[str, Any]:
    """
    Decode and verify a JWT.
    Raises jwt.PyJWTError on invalid / expired tokens.
    """
    return jwt.decode(
        token,
        settings.jwt_secret,
        algorithms=[settings.jwt_algorithm],
    )
