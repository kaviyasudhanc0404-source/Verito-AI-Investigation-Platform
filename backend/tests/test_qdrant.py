"""
tests/test_qdrant.py
---------------------
Tests for Qdrant Cloud connection, vector upsert, query, and delete.

Requires QDRANT_URL and QDRANT_API_KEY to be set in .env.
Run: pytest tests/test_qdrant.py -v
"""

import pytest
import uuid


@pytest.fixture(scope="module")
def test_investigation_id() -> str:
    """Unique investigation ID for this test run to avoid collisions."""
    return f"test_inv_{uuid.uuid4().hex[:8]}"


def test_qdrant_connection():
    """Test that the Qdrant Cloud client connects successfully."""
    from app.services.vector_store import _get_client, _ensure_collection
    client = _get_client()
    assert client is not None, "Qdrant client should not be None"
    # Ensure collection exists
    _ensure_collection()
    from app.core.config import get_settings
    settings = get_settings()
    existing = [c.name for c in client.get_collections().collections]
    assert settings.qdrant_collection in existing, (
        f"Collection '{settings.qdrant_collection}' should exist after _ensure_collection()"
    )


def test_qdrant_point_ids_are_stable():
    """Retries should upsert the same chunk IDs instead of creating duplicates."""
    from app.services.vector_store import _point_id

    first = _point_id("usr_test001", "inv_123", "E-008", 3)
    second = _point_id("usr_test001", "inv_123", "E-008", 3)
    different_chunk = _point_id("usr_test001", "inv_123", "E-008", 4)

    assert first == second
    assert first != different_chunk


def test_store_evidence_chunks(test_investigation_id):
    """Test storing evidence text chunks in Qdrant Cloud."""
    from app.services.vector_store import store_evidence_chunks

    text = (
        "A black sedan with licence plate TN 33 AB 1234 was spotted near the warehouse "
        "on the night of the incident. The vehicle was parked for approximately 45 minutes "
        "before driving away at high speed towards the eastern highway."
    )

    count = store_evidence_chunks(
        text=text,
        user_id="usr_test001",
        investigation_id=test_investigation_id,
        evidence_id="E-001",
        filename="witness_statement.txt",
    )

    assert count > 0, f"Expected at least 1 chunk stored, got {count}"
    print(f"  ✓ Stored {count} chunk(s)")


def test_query_evidence(test_investigation_id):
    """Test querying evidence from Qdrant Cloud."""
    from app.services.vector_store import query_evidence

    results = query_evidence(
        query="What vehicle was spotted near the warehouse?",
        investigation_id=test_investigation_id,
        n_results=3,
    )

    assert isinstance(results, list), "Results should be a list"
    assert len(results) > 0, "Should return at least 1 result"

    first = results[0]
    assert "text" in first, "Result should have 'text' key"
    assert "evidence_id" in first, "Result should have 'evidence_id' key"
    assert "score" in first, "Result should have 'score' key"
    assert first["score"] > 0, "Score should be positive"
    print(f"  ✓ Query returned {len(results)} result(s), top score: {first['score']}")


def test_delete_investigation_chunks(test_investigation_id):
    """Test deleting all chunks for an investigation from Qdrant Cloud."""
    from app.services.vector_store import delete_investigation_chunks, query_evidence

    # Delete all chunks
    delete_investigation_chunks(test_investigation_id)

    # Verify they are gone
    results = query_evidence(
        query="black sedan warehouse",
        investigation_id=test_investigation_id,
        n_results=5,
    )
    assert len(results) == 0, (
        f"Expected 0 results after deletion, got {len(results)}"
    )
    print("  ✓ All chunks deleted successfully")
