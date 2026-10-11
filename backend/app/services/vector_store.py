"""
services/vector_store.py
-------------------------
Qdrant Cloud client and vector storage operations.
This module replaces chroma_service.py.

All Qdrant interactions are centralised here — agents, RAG, and the
evidence collector import from this module.

Connection:
  URL:     QDRANT_URL   (from .env)
  API Key: QDRANT_API_KEY (from .env)
  Collection: QDRANT_COLLECTION (default: verito_evidence)
  Distance:   COSINE

Collection schema (payload fields stored per vector):
  - user_id
  - investigation_id
  - evidence_id
  - filename
  - chunk_index
  - text  (the actual chunk content)
"""

from __future__ import annotations

import uuid
from typing import Any

from app.core.config import get_settings
from app.core.exceptions import ServiceException
from app.services.embedding_service import embed_texts, embed_query, EMBEDDING_DIMENSION
from app.services.chunking_service import chunk_text

settings = get_settings()

# Module-level singleton Qdrant client
_client: Any | None = None
_POINT_ID_NAMESPACE = uuid.UUID("6f60f3a2-47d0-4e5e-9c3d-cb2cfe80ec5f")


def _get_client() -> Any:
    """Lazy-load and return the Qdrant Cloud client singleton."""
    global _client
    if _client is not None:
        return _client

    if not settings.qdrant_url or not settings.qdrant_api_key:
        raise ServiceException(
            "Qdrant Cloud is not configured. "
            "Set QDRANT_URL and QDRANT_API_KEY in your .env file."
        )

    try:
        from qdrant_client import QdrantClient
    except ImportError as exc:
        raise ServiceException(
            "qdrant-client is not installed. Run: pip install qdrant-client"
        ) from exc

    _client = QdrantClient(
        url=settings.qdrant_url,
        api_key=settings.qdrant_api_key,
        timeout=30,
    )
    return _client


def _ensure_collection() -> None:
    """
    Create the Qdrant collection if it does not already exist.
    Uses COSINE distance to match the normalised BGE embeddings.
    Also ensures a keyword payload index exists on 'investigation_id'
    so filtered queries and deletes work without an error.
    """
    from qdrant_client.models import Distance, VectorParams, PayloadSchemaType

    client = _get_client()
    collection_name = settings.qdrant_collection

    existing = [c.name for c in client.get_collections().collections]
    if collection_name not in existing:
        client.create_collection(
            collection_name=collection_name,
            vectors_config=VectorParams(
                size=EMBEDDING_DIMENSION,
                distance=Distance.COSINE,
            ),
        )

    # Ensure payload index on investigation_id for filtered queries / deletes.
    # This is idempotent — safe to call every startup.
    try:
        client.create_payload_index(
            collection_name=collection_name,
            field_name="investigation_id",
            field_schema=PayloadSchemaType.KEYWORD,
        )
    except Exception:
        pass  # Index already exists — not an error

    try:
        client.create_payload_index(
            collection_name=collection_name,
            field_name="user_id",
            field_schema=PayloadSchemaType.KEYWORD,
        )
    except Exception:
        pass

    # Ensure payload index on evidence_id for deduplication deletes.
    try:
        client.create_payload_index(
            collection_name=collection_name,
            field_name="evidence_id",
            field_schema=PayloadSchemaType.KEYWORD,
        )
    except Exception:
        pass  # Index already exists — not an error


# ── Public API ────────────────────────────────────────────────────────────────

def store_evidence_chunks(
    *,
    text: str,
    user_id: str,
    investigation_id: str,
    evidence_id: str,
    filename: str,
    chunks: list[str] | None = None,
) -> int:
    """
    Embed each chunk and upsert into Qdrant Cloud.

    If `chunks` is provided (pre-computed by the caller) it is used directly —
    no re-chunking occurs.  If omitted, `text` is chunked internally.
    Passing pre-computed chunks avoids double-chunking when evidence_collector
    already chunked the text for logging/validation purposes.

    Args:
        text:              Full cleaned evidence text (used only if chunks=None).
        user_id:           ID of the owning user.
        investigation_id:  ID of the parent investigation.
        evidence_id:       Evidence identifier (e.g. E-001).
        filename:          Original filename.
        chunks:            Optional pre-computed list of chunk strings.

    Returns:
        Number of chunks stored (0 if text is empty or no chunks produced).
    """
    from qdrant_client.models import PointStruct

    # Use caller-supplied chunks if provided; otherwise chunk internally.
    # Chunks are normally strings, but dict chunks are accepted so future
    # page-aware parsers can pass metadata without changing this API.
    if chunks is None:
        chunks = chunk_text(text)
    if not chunks:
        return 0

    _ensure_collection()
    client = _get_client()

    # ── Deduplication: remove existing chunks for this evidence_id ─────────
    # Prevents duplicate vectors when re-processing the same evidence file.
    try:
        from qdrant_client.models import Filter, FieldCondition, MatchValue
        client.delete(
            collection_name=settings.qdrant_collection,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="user_id",
                        match=MatchValue(value=user_id),
                    ),
                    FieldCondition(
                        key="evidence_id",
                        match=MatchValue(value=evidence_id),
                    ),
                    FieldCondition(
                        key="investigation_id",
                        match=MatchValue(value=investigation_id),
                    ),
                ]
            ),
        )
    except Exception:
        pass  # Collection may not exist yet or no prior points — safe to ignore

    prepared_chunks = [
        {
            "text": chunk.get("text", "") if isinstance(chunk, dict) else str(chunk),
            "page": chunk.get("page") if isinstance(chunk, dict) else None,
        }
        for chunk in chunks
    ]
    prepared_chunks = [chunk for chunk in prepared_chunks if chunk["text"]]
    if not prepared_chunks:
        return 0

    embeddings = embed_texts([chunk["text"] for chunk in prepared_chunks])

    points = [
        PointStruct(
            id=_point_id(user_id, investigation_id, evidence_id, i),
            vector=embeddings[i],
            payload={
                "user_id": user_id,
                "investigation_id": investigation_id,
                "evidence_id": evidence_id,
                "filename": filename,
                "chunk_index": i,
                "page": prepared_chunks[i]["page"],
                "text": prepared_chunks[i]["text"],  # store chunk text in payload for retrieval
            },
        )
        for i in range(len(prepared_chunks))
    ]

    client.upsert(
        collection_name=settings.qdrant_collection,
        points=points,
    )

    return len(points)


def _point_id(user_id: str, investigation_id: str, evidence_id: str, chunk_index: int) -> str:
    """Stable point IDs make repeated upserts idempotent after network retries."""
    raw = f"{user_id}|{investigation_id}|{evidence_id}|{chunk_index}"
    return str(uuid.uuid5(_POINT_ID_NAMESPACE, raw))


def query_evidence(
    *,
    query: str,
    investigation_id: str,
    user_id: str | None = None,
    n_results: int = 5,
) -> list[dict]:
    """
    Query Qdrant Cloud for chunks semantically similar to `query`,
    filtered to a specific investigation.

    Args:
        query: Natural language question or search string.
        investigation_id: Filter results to this investigation only.
        user_id: Optional owner filter. Pass this in multi-user API calls.
        n_results: Maximum number of results to return.

    Returns:
        List of dicts with keys: text, evidence_id, filename, score.
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    _ensure_collection()
    client = _get_client()

    query_vector = embed_query(query)

    must = [
        FieldCondition(
            key="investigation_id",
            match=MatchValue(value=investigation_id),
        )
    ]
    if user_id:
        must.append(
            FieldCondition(
                key="user_id",
                match=MatchValue(value=user_id),
            )
        )

    search_filter = Filter(must=must)

    # client.search() was removed in qdrant-client >= 1.7.4.
    # Use query_points() which returns a QueryResponse with a .points list.
    response = client.query_points(
        collection_name=settings.qdrant_collection,
        query=query_vector,
        query_filter=search_filter,
        limit=n_results,
        with_payload=True,
    )

    output: list[dict] = []
    for hit in response.points:
        payload = hit.payload or {}
        output.append(
            {
                "text": payload.get("text", ""),
                "evidence_id": payload.get("evidence_id", ""),
                "filename": payload.get("filename", ""),
                "score": round(float(hit.score), 4),
            }
        )

    return output


def get_investigation_chunks(
    *,
    investigation_id: str,
    user_id: str | None = None,
    batch_size: int = 256,
) -> list[dict]:
    """
    Fetch every stored Qdrant chunk for an investigation.

    This is used by analysis agents that need complete coverage of the evidence,
    unlike semantic search which returns only the chunks matching a query.
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    _ensure_collection()
    client = _get_client()

    must = [
        FieldCondition(
            key="investigation_id",
            match=MatchValue(value=investigation_id),
        )
    ]
    if user_id:
        must.append(
            FieldCondition(
                key="user_id",
                match=MatchValue(value=user_id),
            )
        )

    chunks: list[dict] = []
    next_page_offset = None

    while True:
        points, next_page_offset = client.scroll(
            collection_name=settings.qdrant_collection,
            scroll_filter=Filter(must=must),
            limit=batch_size,
            offset=next_page_offset,
            with_payload=True,
            with_vectors=False,
        )

        for point in points:
            payload = point.payload or {}
            text = payload.get("text", "")
            if not text:
                continue
            chunks.append(
                {
                    "text": text,
                    "investigation_id": payload.get("investigation_id", ""),
                    "evidence_id": payload.get("evidence_id", ""),
                    "filename": payload.get("filename", ""),
                    "chunk_index": payload.get("chunk_index", 0),
                    "page": payload.get("page"),
                    "user_id": payload.get("user_id", ""),
                }
            )

        if next_page_offset is None:
            break

    return sorted(
        chunks,
        key=lambda chunk: (
            str(chunk.get("evidence_id", "")),
            int(chunk.get("chunk_index") or 0),
        ),
    )


def delete_investigation_chunks(investigation_id: str, user_id: str | None = None) -> None:
    """
    Delete all vector points belonging to a specific investigation.

    Args:
        investigation_id: The investigation whose chunks should be removed.
        user_id: Optional owner filter for multi-user isolation.
    """
    from qdrant_client.models import Filter, FieldCondition, MatchValue

    _ensure_collection()
    client = _get_client()

    # Use delete_vectors filter — works in qdrant-client >= 1.9 without FilterSelector
    must = [
        FieldCondition(
            key="investigation_id",
            match=MatchValue(value=investigation_id),
        )
    ]
    if user_id:
        must.append(
            FieldCondition(
                key="user_id",
                match=MatchValue(value=user_id),
            )
        )

    client.delete(
        collection_name=settings.qdrant_collection,
        points_selector=Filter(must=must),
    )
