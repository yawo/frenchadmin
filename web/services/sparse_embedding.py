from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_model = None
_tokenizer = None


def _get_model():
    """Lazy-load BGE-M3 via FlagEmbedding for sparse retrieval."""
    global _model
    if _model is None:
        from FlagEmbedding import BGEM3FlagModel

        _model = BGEM3FlagModel("BAAI/bge-m3", use_fp16=True)
        logger.info("Loaded BGE-M3 sparse model (FlagEmbedding)")
    return _model


def _get_tokenizer():
    """Get the tokenizer for decoding token IDs to text."""
    global _tokenizer
    if _tokenizer is None:
        from transformers import AutoTokenizer

        _tokenizer = AutoTokenizer.from_pretrained("BAAI/bge-m3")
    return _tokenizer


def _decode_sparse(token_weights: dict[int, float], top_k: int = 256) -> dict[str, float]:
    """Convert {token_id: weight} to {text_token: weight}, keeping top_k by weight."""
    if not token_weights:
        return {}
    tokenizer = _get_tokenizer()
    sorted_items = sorted(token_weights.items(), key=lambda x: x[1], reverse=True)[:top_k]
    decoded = {}
    for token_id, weight in sorted_items:
        if weight <= 0:
            break
        token_text = tokenizer.decode([token_id]).strip()
        if token_text and len(token_text) > 1:
            decoded[token_text.lower()] = float(weight)
    return decoded


def encode_sparse(texts: list[str]) -> list[dict[str, float]]:
    """Encode texts into sparse embeddings (top-256 decoded tokens with weights).

    Returns a list of {token_text: weight} dicts.
    """
    if not texts:
        return []
    model = _get_model()
    output = model.encode(texts, return_dense=False, return_sparse=True, return_colbert_vecs=False)
    sparse_vecs = output["lexical_weights"]
    return [_decode_sparse(sv, top_k=256) for sv in sparse_vecs]


def encode_sparse_query(text: str) -> dict[str, float]:
    """Encode a single query into sparse embedding."""
    results = encode_sparse([text])
    return results[0] if results else {}
