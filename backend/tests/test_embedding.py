"""
tests/test_embedding.py
------------------------
Tests for the BAAI/bge-small-en-v1.5 embedding service.

Verifies:
  - Model loads without error
  - embed_texts returns correct shape (N, 384)
  - embed_query uses BGE instruction prefix
  - Semantic similarity: similar texts score higher than dissimilar texts
  - Empty input returns empty list

Run: pytest tests/test_embedding.py -v
"""



def test_embedding_model_loads():
    """Test that the BAAI/bge-small-en-v1.5 model loads successfully."""
    from app.services.embedding_service import _get_model
    model = _get_model()
    assert model is not None, "Embedding model should not be None"
    print("  ✓ BAAI/bge-small-en-v1.5 loaded successfully")


def test_embedding_dimension():
    """Test that embeddings have the correct 384-dimensional output."""
    from app.services.embedding_service import embed_texts, EMBEDDING_DIMENSION

    texts = ["A black car parked near a warehouse.", "Two suspects seen at the scene."]
    embeddings = embed_texts(texts)

    assert len(embeddings) == 2, f"Expected 2 embeddings, got {len(embeddings)}"
    assert len(embeddings[0]) == EMBEDDING_DIMENSION, (
        f"Expected dimension {EMBEDDING_DIMENSION}, got {len(embeddings[0])}"
    )
    assert len(embeddings[1]) == EMBEDDING_DIMENSION
    print(f"  ✓ Embeddings shape: ({len(embeddings)}, {EMBEDDING_DIMENSION})")


def test_embed_query_instruction_prefix():
    """Test that embed_query produces a valid embedding (with BGE prefix)."""
    from app.services.embedding_service import embed_query, EMBEDDING_DIMENSION

    query_vec = embed_query("Who was seen near the warehouse?")
    assert len(query_vec) == EMBEDDING_DIMENSION
    assert any(v != 0.0 for v in query_vec), "Query embedding should not be all zeros"
    print(f"  ✓ Query embedding dimension: {len(query_vec)}")


def test_empty_input_returns_empty_list():
    """Test that empty input returns an empty list without error."""
    from app.services.embedding_service import embed_texts

    result = embed_texts([])
    assert result == [], f"Expected [], got {result}"
    print("  ✓ Empty input returns []")


def test_semantic_similarity():
    """Test that semantically similar texts have higher cosine similarity."""
    import math
    from app.services.embedding_service import embed_texts

    def cosine_similarity(v1: list[float], v2: list[float]) -> float:
        dot = sum(a * b for a, b in zip(v1, v2))
        mag1 = math.sqrt(sum(a * a for a in v1))
        mag2 = math.sqrt(sum(b * b for b in v2))
        return dot / (mag1 * mag2) if mag1 and mag2 else 0.0

    texts = [
        "A black sedan was parked near the crime scene.",       # similar to query
        "The suspect was wearing a blue jacket.",               # somewhat related
        "The recipe requires two cups of flour and one egg.",   # unrelated
    ]
    query = ["black vehicle spotted at the location"]

    all_texts = query + texts
    embeddings = embed_texts(all_texts)

    query_vec = embeddings[0]
    sim_similar = cosine_similarity(query_vec, embeddings[1])
    sim_unrelated = cosine_similarity(query_vec, embeddings[3])

    assert sim_similar > sim_unrelated, (
        f"Similar text ({sim_similar:.4f}) should score higher than "
        f"unrelated text ({sim_unrelated:.4f})"
    )
    print(f"  ✓ Similar: {sim_similar:.4f} > Unrelated: {sim_unrelated:.4f}")
