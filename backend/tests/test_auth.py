"""
tests/test_auth.py
------------------
Tests for authentication endpoints.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch, MagicMock

from app.main import app


@pytest.mark.asyncio
async def test_signup_success():
    """Test that a new user can sign up and receives a JWT."""
    mock_user = {
        "_id": "usr_test001",
        "email": "test@verito.ai",
        "name": "Test User",
        "password_hash": "hashed",
        "google_uid": None,
        "created_at": None,
    }

    with patch("app.services.auth_service.signup", new=AsyncMock(return_value=mock_user)):
        with patch("app.services.auth_service.make_token_response") as mock_token:
            mock_token.return_value = {
                "access_token": "test.jwt.token",
                "token_type": "bearer",
                "user_id": "usr_test001",
                "name": "Test User",
                "email": "test@verito.ai",
            }
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.post(
                    "/api/auth/signup",
                    json={
                        "name": "Test User",
                        "email": "test@verito.ai",
                        "password": "password123",
                    },
                )

    assert response.status_code == 201
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"


@pytest.mark.asyncio
async def test_signup_short_password():
    """Test that signup rejects passwords shorter than 8 characters."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/auth/signup",
            json={
                "name": "Test User",
                "email": "test@verito.ai",
                "password": "short",
            },
        )
    assert response.status_code == 422  # Validation error


@pytest.mark.asyncio
async def test_login_wrong_password():
    """Test that login with wrong credentials returns 401."""
    from app.core.exceptions import UnauthorizedException

    with patch(
        "app.services.auth_service.login",
        new=AsyncMock(side_effect=UnauthorizedException("Invalid email or password.")),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            response = await ac.post(
                "/api/auth/login",
                json={"email": "test@verito.ai", "password": "wrongpass"},
            )
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_me_requires_auth():
    """Test that /api/auth/me returns 403 without a token."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/auth/me")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_health_check():
    """Test the health check endpoint."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
