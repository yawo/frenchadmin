from __future__ import annotations

from sentence_transformers import CrossEncoder

from config import RERANKER_BATCH_SIZE, RERANKER_MAX_LENGTH, RERANKER_MODEL

_model: CrossEncoder | None = None


def get_reranker() -> CrossEncoder:
    global _model
    if _model is None:
        _model = CrossEncoder(RERANKER_MODEL, max_length=RERANKER_MAX_LENGTH)
    return _model


def rerank(query: str, documents: list[str], top_k: int = 10) -> list[tuple[int, float]]:
    """Rerank documents by cross-encoder relevance to query.

    Returns list of (original_index, score) sorted by score descending.
    """
    if not documents:
        return []
    model = get_reranker()
    pairs = [[query, doc] for doc in documents]
    scores = model.predict(pairs, batch_size=RERANKER_BATCH_SIZE)
    ranked = sorted(enumerate(scores), key=lambda x: x[1], reverse=True)
    return ranked[:top_k]
