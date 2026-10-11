"""
dependencies/auth.py
---------------------
FastAPI dependency that extracts and verifies the JWT from the Authorization header.
Use `Depends(get_current_user)` in any protected route, and
`Depends(get_owned_investigation)` in routes with an {investigation_id} path parameter.
"""

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import ForbiddenException, NotFoundException, UnauthorizedException
from app.core.security import decode_access_token
from app.services.auth_service import get_user_by_id
from app.services import investigation_service

import jwt

# HTTP Bearer token extractor (reads Authorization: Bearer <token>)
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
) -> dict:
    """
    Verify the JWT and return the current user document from MongoDB.
    Raises UnauthorizedException if the token is missing, invalid, or expired.
    """
    if not credentials:
        raise UnauthorizedException("No authentication token provided.")

    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.ExpiredSignatureError:
        raise UnauthorizedException("Token has expired. Please log in again.")
    except jwt.PyJWTError:
        raise UnauthorizedException("Invalid authentication token.")

    user_id: str | None = payload.get("sub")
    if not user_id:
        raise UnauthorizedException("Malformed token: missing subject.")

    user = await get_user_by_id(user_id)
    if not user:
        raise UnauthorizedException("User not found.")

    return user


async def get_owned_investigation(
    investigation_id: str,
    current_user: dict = Depends(get_current_user),
) -> dict:
    """Return the investigation if it exists and belongs to the current user."""
    inv = await investigation_service.get_investigation(investigation_id)
    if not inv:
        raise NotFoundException(f"Investigation '{investigation_id}' not found.")
    if inv["user_id"] != current_user["_id"]:
        raise ForbiddenException("You do not have access to this investigation.")
    return inv
