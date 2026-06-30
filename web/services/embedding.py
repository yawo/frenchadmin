from config import EMBEDDING_MODEL
from utils import format_model_name, generate_embeddings_with_retry

_EMBEDDING_COL_SUFFIX = format_model_name(EMBEDDING_MODEL)
EMBEDDING_COLUMN = f"embeddings_{_EMBEDDING_COL_SUFFIX}"


def embed_query(text: str) -> list[float]:
    """Generate embedding for a single query text using the configured model."""
    embeddings = generate_embeddings_with_retry(data=text, model=EMBEDDING_MODEL)
    return embeddings[0]
