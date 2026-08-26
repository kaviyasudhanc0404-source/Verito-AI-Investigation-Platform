"""
tests/test_investigations.py
-----------------------------
Tests for investigation CRUD endpoints.
"""

import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch, MagicMock

from app.main import app
from app.dependencies.auth import get_current_user


def _make_auth_headers(token: str = "test.jwt.token") -> dict:
    return {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_list_investigations_requires_auth():
    """Test that listing investigations requires authentication."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.get("/api/investigations")
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_create_investigation_requires_auth():
    """Test that creating an investigation requires authentication."""
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        response = await ac.post(
            "/api/investigations",
            json={"title": "Test Case", "prompt": "Test prompt"},
        )
    assert response.status_code in (401, 403)


@pytest.mark.asyncio
async def test_delete_nonexistent_investigation():
    """Test that deleting a non-existent investigation returns 404."""
    mock_user = {"_id": "usr_test001", "email": "test@verito.ai", "name": "Test User"}

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch(
            "app.services.investigation_service.get_investigation",
            new=AsyncMock(return_value=None),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.delete(
                    "/api/investigations/inv_nonexistent",
                    headers=_make_auth_headers(),
                )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_get_investigation_wrong_user():
    """Test that a user cannot access another user's investigation."""
    mock_user = {"_id": "usr_other", "email": "other@verito.ai", "name": "Other User"}
    mock_inv = {
        "_id": "inv_abc123",
        "user_id": "usr_owner",  # Different user
        "title": "Secret Case",
        "prompt": "...",
        "status": "complete",
    }

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with patch(
            "app.services.investigation_service.get_investigation",
            new=AsyncMock(return_value=mock_inv),
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.get(
                    "/api/investigations/inv_abc123",
                    headers=_make_auth_headers(),
                )
    finally:
        app.dependency_overrides.clear()
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_update_investigation_rename_and_pin():
    """Test that rename/pin updates are accepted for the owning user."""
    mock_user = {"_id": "usr_owner", "email": "owner@verito.ai", "name": "Owner"}
    mock_inv = {
        "_id": "inv_abc123",
        "user_id": "usr_owner",
        "title": "Old Case",
        "prompt": "...",
        "status": "complete",
    }
    updated_inv = {
        **mock_inv,
        "title": "Renamed Case",
        "pinned": True,
        "created_at": "2026-08-07T10:00:00Z",
    }

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with (
            patch(
                "app.services.investigation_service.get_investigation",
                new=AsyncMock(return_value=mock_inv),
            ),
            patch(
                "app.services.investigation_service.update_investigation",
                new=AsyncMock(return_value=updated_inv),
            ) as update_mock,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.patch(
                    "/api/investigations/inv_abc123",
                    json={"title": "Renamed Case", "pinned": True},
                    headers=_make_auth_headers(),
                )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 200
    assert response.json()["title"] == "Renamed Case"
    assert response.json()["pinned"] is True
    update_mock.assert_awaited_once_with(
        "inv_abc123",
        title="Renamed Case",
        pinned=True,
    )


@pytest.mark.asyncio
async def test_delete_investigation_cleans_qdrant_with_user_filter():
    """Test delete requests remove vectors using both investigation and user IDs."""
    mock_user = {"_id": "usr_owner", "email": "owner@verito.ai", "name": "Owner"}
    mock_inv = {
        "_id": "inv_abc123",
        "user_id": "usr_owner",
        "title": "Case",
        "prompt": "...",
        "status": "complete",
    }

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with (
            patch(
                "app.services.investigation_service.get_investigation",
                new=AsyncMock(return_value=mock_inv),
            ),
            patch(
                "app.services.vector_store.delete_investigation_chunks",
                new=MagicMock(),
            ) as qdrant_delete,
            patch(
                "app.services.investigation_service.delete_investigation",
                new=AsyncMock(),
            ) as mongo_delete,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.delete(
                    "/api/investigations/inv_abc123",
                    headers=_make_auth_headers(),
                )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 204
    qdrant_delete.assert_called_once_with("inv_abc123", "usr_owner")
    mongo_delete.assert_awaited_once_with("inv_abc123", "usr_owner")


@pytest.mark.asyncio
async def test_delete_investigation_does_not_orphan_qdrant_on_failure():
    """If Qdrant cleanup fails, Mongo metadata remains so deletion can be retried."""
    mock_user = {"_id": "usr_owner", "email": "owner@verito.ai", "name": "Owner"}
    mock_inv = {
        "_id": "inv_abc123",
        "user_id": "usr_owner",
        "title": "Case",
        "prompt": "...",
        "status": "complete",
    }

    app.dependency_overrides[get_current_user] = lambda: mock_user
    try:
        with (
            patch(
                "app.services.investigation_service.get_investigation",
                new=AsyncMock(return_value=mock_inv),
            ),
            patch(
                "app.services.vector_store.delete_investigation_chunks",
                new=MagicMock(side_effect=RuntimeError("qdrant unavailable")),
            ) as qdrant_delete,
            patch(
                "app.services.investigation_service.delete_investigation",
                new=AsyncMock(),
            ) as mongo_delete,
        ):
            async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
                response = await ac.delete(
                    "/api/investigations/inv_abc123",
                    headers=_make_auth_headers(),
                )
    finally:
        app.dependency_overrides.clear()

    assert response.status_code == 500
    qdrant_delete.assert_called_once_with("inv_abc123", "usr_owner")
    mongo_delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_investigation_removes_only_selected_chat_mongo_artifacts():
    """Mongo cleanup is scoped to the deleted investigation and user where available."""
    from app.services import investigation_service

    class FakeCursor:
        async def to_list(self, length=None):
            return []

    investigations = MagicMock()
    investigations.delete_one = AsyncMock()

    evidence = MagicMock()
    evidence.find.return_value = FakeCursor()
    evidence.delete_many = AsyncMock()

    agent_runs = MagicMock()
    agent_runs.delete_many = AsyncMock()

    reports = MagicMock()
    reports.delete_many = AsyncMock()

    chat_messages = MagicMock()
    chat_messages.delete_many = AsyncMock()

    with (
        patch("app.services.investigation_service._delete_investigation_uploads"),
        patch("app.database.collections.investigations", return_value=investigations),
        patch("app.database.collections.evidence", return_value=evidence),
        patch("app.database.collections.agent_runs", return_value=agent_runs),
        patch("app.database.collections.reports", return_value=reports),
        patch("app.database.collections.chat_messages", return_value=chat_messages),
    ):
        await investigation_service.delete_investigation("inv_keep_scope", "usr_owner")

    scoped_user_filter = {"investigation_id": "inv_keep_scope", "user_id": "usr_owner"}
    evidence.find.assert_called_once_with(scoped_user_filter)
    investigations.delete_one.assert_awaited_once_with(
        {"_id": "inv_keep_scope", "user_id": "usr_owner"}
    )
    evidence.delete_many.assert_awaited_once_with(scoped_user_filter)
    agent_runs.delete_many.assert_awaited_once_with({"investigation_id": "inv_keep_scope"})
    reports.delete_many.assert_awaited_once_with({"investigation_id": "inv_keep_scope"})
    chat_messages.delete_many.assert_awaited_once_with(scoped_user_filter)
