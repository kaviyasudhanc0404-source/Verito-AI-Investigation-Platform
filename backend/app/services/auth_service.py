"""
services/auth_service.py
-------------------------
Business logic for authentication: signup, login, Google login.
"""

from datetime import datetime, timezone
from typing import Optional

import firebase_admin
from firebase_admin import auth as firebase_auth, credentials

from app.core.config import get_settings
from app.core.exceptions import ConflictException, UnauthorizedException, BadRequestException
from app.core.security import hash_password, verify_password, create_access_token
from app.database import collections
from app.utils.id_generator import new_user_id
from app.utils.datetime_utils import utcnow

settings = get_settings()

# ── Firebase Admin SDK initialisation (once) ─────────────────────────────────

_firebase_initialised = False


def _init_firebase() -> None:
    global _firebase_initialised
    if _firebase_initialised:
        return
    cred = credentials.Certificate(
        {
            "type": "service_account",
            "project_id": settings.firebase_project_id,
            "client_email": settings.firebase_client_email,
            "private_key": settings.firebase_private_key.replace("\\n", "\n"),
            "token_uri": "https://oauth2.googleapis.com/token",
        }
    )
    firebase_admin.initialize_app(cred)
    _firebase_initialised = True


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_token(user_doc: dict) -> str:
    return create_access_token(
        {"sub": user_doc["_id"], "email": user_doc["email"]}
    )


async def _find_user_by_email(email: str) -> Optional[dict]:
    return await collections.users().find_one({"email": email})


async def _find_user_by_id(user_id: str) -> Optional[dict]:
    return await collections.users().find_one({"_id": user_id})


# ── Public service functions ──────────────────────────────────────────────────

async def signup(name: str, email: str, password: str) -> dict:
    """
    Create a new account with email + password.
    Returns the new user document.
    Raises ConflictException if email is already taken.
    """
    existing = await _find_user_by_email(email)
    if existing:
        raise ConflictException("An account with this email already exists.")

    user_doc = {
        "_id": new_user_id(),
        "email": email,
        "name": name,
        "password_hash": hash_password(password),
        "google_uid": None,
        "created_at": utcnow(),
    }
    await collections.users().insert_one(user_doc)
    return user_doc


async def login(email: str, password: str) -> dict:
    """
    Verify email + password.
    Returns the user document.
    Raises UnauthorizedException on bad credentials.
    """
    user = await _find_user_by_email(email)
    if not user or not user.get("password_hash"):
        raise UnauthorizedException("Invalid email or password.")

    if not verify_password(password, user["password_hash"]):
        raise UnauthorizedException("Invalid email or password.")

    return user


async def google_login(id_token: str) -> dict:
    """
    Verify a Firebase ID token and find-or-create the user.
    Returns the user document.
    """
    _init_firebase()

    try:
        decoded = firebase_auth.verify_id_token(id_token)
    except Exception as e:
        raise UnauthorizedException(f"Invalid Google token: {e}")

    google_uid: str = decoded["uid"]
    email: str = decoded.get("email", "")
    name: str = decoded.get("name", email.split("@")[0])

    # Look for existing account by Google UID or email
    user = await collections.users().find_one(
        {"$or": [{"google_uid": google_uid}, {"email": email}]}
    )

    if user:
        # Update google_uid if missing
        if not user.get("google_uid"):
            await collections.users().update_one(
                {"_id": user["_id"]},
                {"$set": {"google_uid": google_uid}},
            )
        return user

    # Create new user
    user_doc = {
        "_id": new_user_id(),
        "email": email,
        "name": name,
        "password_hash": None,
        "google_uid": google_uid,
        "created_at": utcnow(),
    }
    await collections.users().insert_one(user_doc)
    return user_doc


async def get_user_by_id(user_id: str) -> Optional[dict]:
    """Fetch a user document by ID."""
    return await _find_user_by_id(user_id)


def make_token_response(user_doc: dict) -> dict:
    """Build the token response payload from a user document."""
    return {
        "access_token": _build_token(user_doc),
        "token_type": "bearer",
        "user_id": user_doc["_id"],
        "name": user_doc["name"],
        "email": user_doc["email"],
    }
