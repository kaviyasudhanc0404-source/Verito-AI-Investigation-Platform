"""
schemas/auth.py
---------------
Request and response schemas for authentication endpoints.
"""

from pydantic import BaseModel, EmailStr, field_validator


class SignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str

    @field_validator("password")
    @classmethod
    def password_min_length(cls, v: str) -> str:
        if len(v) < 8:
            raise ValueError("Password must be at least 8 characters")
        return v

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Name cannot be empty")
        return v.strip()


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class GoogleLoginRequest(BaseModel):
    """Firebase ID token sent from the frontend after Google sign-in."""
    id_token: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    name: str
    email: str


class UpdateProfileRequest(BaseModel):
    name: str | None = None
    email: EmailStr | None = None
    current_password: str | None = None
    new_password: str | None = None

    @field_validator("new_password")
    @classmethod
    def new_password_min_length(cls, v: str | None) -> str | None:
        if v is not None and len(v) < 8:
            raise ValueError("New password must be at least 8 characters")
        return v


class UserResponse(BaseModel):
    user_id: str
    name: str
    email: str
