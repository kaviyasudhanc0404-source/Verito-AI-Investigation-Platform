"""
api/auth.py
-----------
Authentication endpoints: signup, login, Google login, current user.
"""

from fastapi import APIRouter, Depends

from app.dependencies.auth import get_current_user
from app.schemas.auth import (
    SignupRequest,
    LoginRequest,
    GoogleLoginRequest,
    TokenResponse,
    UserResponse,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post("/signup", response_model=TokenResponse, status_code=201)
async def signup(body: SignupRequest) -> TokenResponse:
    """Create a new account and return a JWT."""
    user = await auth_service.signup(
        name=body.name,
        email=str(body.email),
        password=body.password,
    )
    return auth_service.make_token_response(user)


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    """Login with email and password, return a JWT."""
    user = await auth_service.login(
        email=str(body.email),
        password=body.password,
    )
    return auth_service.make_token_response(user)


@router.post("/google", response_model=TokenResponse)
async def google_login(body: GoogleLoginRequest) -> TokenResponse:
    """
    Verify a Firebase ID token from Google Sign-In.
    Find or create the user and return a VERITO JWT.
    """
    user = await auth_service.google_login(id_token=body.id_token)
    return auth_service.make_token_response(user)


@router.get("/me", response_model=UserResponse)
async def me(current_user: dict = Depends(get_current_user)) -> UserResponse:
    """Return the currently authenticated user's profile."""
    return UserResponse(
        user_id=current_user["_id"],
        name=current_user["name"],
        email=current_user["email"],
    )
