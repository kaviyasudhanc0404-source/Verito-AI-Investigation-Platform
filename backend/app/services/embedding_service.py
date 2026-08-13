"""
services/embedding_service.py
------------------------------
Generate text embeddings for evidence chunks using BAAI/bge-small-en-v1.5.

Model: BAAI/bge-small-en-v1.5
  - Embedding dimension: 384
  - Optimised for English semantic similarity / retrieval tasks
  - Runs locally — no API key required
  - BGE models work best with a query instruction prefix for queries
    (but NOT for document chunks being stored)

The model is lazy-loaded and cached as a module-level singleton.
"""

from __future__ import annotations

from typing import Any

# Module-level singleton — loaded once on first use
_model: Any | None = None

# Embedding dimension for BAAI/bge-small-en-v1.5
EMBEDDING_DIMENSION = 384

# BGE query instruction prefix (only used for query embeddings, not documents)
_BGE_QUERY_INSTRUCTION = "Represent this sentence for searching relevant passages: "


def _get_model() -> Any:
    """Lazy-load and return the SentenceTransformer model instance."""
    global _model
    if _model is None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise ImportError(
                "sentence-transformers is not installed. "
                "Run: pip install sentence-transformers"
            ) from exc
        _model = SentenceTransformer("BAAI/bge-small-en-v1.5")
    return _model


def embed_texts(texts: list[str]) -> list[list[float]]:
    """
    Generate embeddings for a list of text strings (document chunks).

    Args:
        texts: List of text strings to embed.

    Returns:
        List of float vectors (one per input text), shape (N, 384).
    """
    if not texts:
        return []
    model = _get_model()
    embeddings = model.encode(
        texts,
        convert_to_numpy=True,
        show_progress_bar=False,
        normalize_embeddings=True,  # BGE models benefit from L2 normalization
    )
    return embeddings.tolist()


def embed_query(query: str) -> list[float]:
    """
    Generate an embedding for a single search query.
    Applies the BGE instruction prefix for better retrieval performance.

    Args:
        query: The user's search query string.

    Returns:
        A single float vector of length 384.
    """
    # BGE models use an instruction prefix for queries (not for stored docs)
    prefixed = _BGE_QUERY_INSTRUCTION + query
    return embed_texts([prefixed])[0]
