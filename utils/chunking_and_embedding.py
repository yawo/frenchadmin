import os
import time
import torch
import xxhash
from langchain.text_splitter import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer

from config import (
    EMBEDDING_MODEL,
    EMBEDDING_BATCH_MAX_SIZE,
    EMBEDDING_RETRY_ATTEMPTS,
    JADE_DATA_FOLDER,
    BOFIP_DATA_FOLDER,
    get_logger,
)

logger = get_logger(__name__)

_tokenizer_cache = {}  # Cache of loaded tokenizers keyed by model name
_embedding_model_cache = {}  # Cache of loaded SentenceTransformer models keyed by model name


def _repair_lemone_position_ids(
    embedding_model: SentenceTransformer, model_name: str
) -> None:
    """Repair invalid position_ids buffer for lemone model after loading.

    Some environments load this model with corrupted position_ids values, which
    later triggers out-of-bounds indexing in rotary embeddings.
    """
    if model_name != "louisbrulenaudet/lemone-embed-pro":
        return

    transformer_module = next(
        (module for module in embedding_model if hasattr(module, "auto_model")),
        None,
    )
    if transformer_module is None:
        return

    auto_model = transformer_module.auto_model
    if not hasattr(auto_model, "embeddings") or not hasattr(
        auto_model.embeddings, "position_ids"
    ):
        return

    max_position_embeddings = int(auto_model.config.max_position_embeddings)
    sample_size = min(64, max_position_embeddings)
    expected_prefix = torch.arange(sample_size, dtype=torch.long)
    current_prefix = (
        auto_model.embeddings.position_ids[:sample_size].detach().to("cpu", torch.long)
    )
    needs_repair = not torch.equal(current_prefix, expected_prefix)
    if not needs_repair:
        return

    auto_model.embeddings.register_buffer(
        "position_ids",
        torch.arange(max_position_embeddings, device=auto_model.device, dtype=torch.long),
        persistent=False,
    )
    logger.warning(
        "Repaired corrupted position_ids buffer for '%s' to prevent rotary embedding index errors.",
        model_name,
    )


def _get_embedding_model(model: str = EMBEDDING_MODEL) -> SentenceTransformer:
    """
    Returns a cached SentenceTransformer model, downloading it from HuggingFace if needed.

    Args:
        model (str): HuggingFace model identifier. Defaults to EMBEDDING_MODEL.

    Returns:
        SentenceTransformer: The loaded embedding model.
    """
    global _embedding_model_cache
    if model not in _embedding_model_cache:
        logger.info(f"Loading embedding model '{model}' from HuggingFace...")
        embedding_model = SentenceTransformer(model, trust_remote_code=True)
        _repair_lemone_position_ids(embedding_model=embedding_model, model_name=model)
        _embedding_model_cache[model] = embedding_model
    return _embedding_model_cache[model]


def generate_embeddings(
    data: str | list[str], model: str = EMBEDDING_MODEL
) -> list[float]:
    """
    Generates embeddings for a given text using a HuggingFace model downloaded locally.

    Args:
        data (str or list[str]): The input to generate embeddings for.
        model (str, optional): The HuggingFace model identifier. Defaults to EMBEDDING_MODEL.

    Returns:
        list[list[float]]: A list of embedding vectors for the input text(s).
    """
    if isinstance(data, str):
        data = [data]
    embedding_model = _get_embedding_model(model)
    vectors = embedding_model.encode(data, convert_to_numpy=True)
    return [v.tolist() if hasattr(v, 'tolist') else list(v) for v in vectors]


def generate_embeddings_with_retry(
    data: str | list[str],
    attempts: int = 5,
    model: str = EMBEDDING_MODEL,
    time_sleep: int = 60,
) -> list[float]:
    """
    Generate embeddings for the provided data with retry mechanism.

    This function attempts to generate embeddings using the local HuggingFace model
    and retries in case of failures.

    Args:
        data (str | list[str]): The text data to generate embeddings for.
            Can be a single string or a list of strings.
        attempts (int, optional): Maximum number of retry attempts. Defaults to 5.
        model (str, optional): The HuggingFace embedding model to use. Defaults to EMBEDDING_MODEL.
        time_sleep (int, optional): Seconds to wait between retries. Defaults to 60.

    Returns:
        list[list[float]]: The generated embeddings as a list of float vectors.

    Raises:
        Exception: If embedding generation fails after all retry attempts.
    """

    for attempt in range(attempts):  # Retry embedding up to {attempts} times
        try:
            embeddings = generate_embeddings(data=data, model=model)
            return embeddings
        except Exception as e:
            if attempt == attempts - 1:  # If this is the last attempt
                logger.error(
                    f"Error generating embeddings for : {str(data)[:200]} ... Error: {e}. Maximum retries reached ({attempts}). Raising exception."
                )
                raise
            logger.error(
                f"Error generating embeddings for : {str(data)[:200]} ... Error: {e}. Retrying in {time_sleep} seconds (attempt {attempt + 1}/{attempts})"
            )
            time.sleep(time_sleep)  # Waiting {time_sleep} seconds before retrying


def embed_texts_with_retry(
    texts: list[str],
    model: str = EMBEDDING_MODEL,
    attempts: int = EMBEDDING_RETRY_ATTEMPTS,
    max_batch_size: int = EMBEDDING_BATCH_MAX_SIZE,
    split_on_failure: bool = True,
    retry_observer=None,
) -> list[list[float]]:
    """Generate embeddings for many texts using bounded batches and split fallback.

    When one batch fails repeatedly, the helper recursively splits the batch
    into two halves to preserve forward progress while keeping output ordering.
    """
    if not texts:
        return []

    def _embed_batch(batch_texts: list[str]) -> list[list[float]]:
        try:
            return generate_embeddings_with_retry(
                data=batch_texts,
                attempts=attempts,
                model=model,
            )
        except Exception:
            if retry_observer is not None:
                try:
                    retry_observer(1)
                except Exception:
                    pass

            if not split_on_failure or len(batch_texts) <= 1:
                raise

            # Binary split fallback to isolate problematic items.
            mid = len(batch_texts) // 2
            left = _embed_batch(batch_texts[:mid])
            right = _embed_batch(batch_texts[mid:])
            return left + right

    embeddings: list[list[float]] = []
    if max_batch_size <= 0:
        max_batch_size = len(texts)

    for i in range(0, len(texts), max_batch_size):
        batch = texts[i : i + max_batch_size]
        embeddings.extend(_embed_batch(batch))
    return embeddings


def _get_length_function(length_function: str):
    """
    Returns the appropriate length function based on the provided string identifier.

    Args:
        length_function (str): The identifier for the desired length function.
            Use "len" for character-based length, "bge_m3_tokenizer" for backwards
            compatibility with the BAAI/bge-m3 tokenizer, or any HuggingFace model
            name to load the corresponding tokenizer.
    """
    global _tokenizer_cache

    if length_function == "len":
        return len

    # "bge_m3_tokenizer" is kept for backwards compatibility
    model_name = "BAAI/bge-m3" if length_function == "bge_m3_tokenizer" else length_function
    if model_name not in _tokenizer_cache:
        _tokenizer_cache[model_name] = AutoTokenizer.from_pretrained(model_name, trust_remote_code=True)
    tokenizer = _tokenizer_cache[model_name]
    return lambda text: len(tokenizer.encode(text))



def make_chunks(
    text: str,
    chunk_size: int = 7500,
    chunk_overlap: int = 500,
    length_function=EMBEDDING_MODEL,
) -> list[str]:
    """
    Splits the input text into overlapping chunks using a recursive character-based text splitter.
    Args:
        text (str): The input text to be split into chunks.
        chunk_size (int, optional): The maximum size of each chunk.
        chunk_overlap (int, optional): The number of overlapping characters between consecutive chunks.
        length_function (str or callable, optional): Controls text length measurement. Accepts "len"
            for character count, "bge_m3_tokenizer" for backwards compatibility, or any HuggingFace
            model name to use the corresponding tokenizer. Defaults to EMBEDDING_MODEL.
    Returns:
        List[str]: A list of text chunks generated from the input text.
    """
    if not text:
        logger.debug("Empty text provided for chunking.")
        return []

    length_fn = _get_length_function(length_function)
    legal_separators = [
        "\nI. ",    # Major divisions (Roman)
        "\nII. ",
        "\nIII. ",
        "\nIV. ",
        "\nV. ",
        
        "\n1° ",      # Numbered lists
        "\n2° ",
        "\n3° ",
        "\n4° ",
        "\n5° ",
        "\na) ",      # Sub-letters
        "\nb) ",
        "\nc) ",      # Sub-letters
        "\nd) ",
        "\ne) ",      # Sub-letters
        "\nA) ",
        "\nB) ",      # Sub-letters
        "\nC) ",
        "\nD) ",
        "\nE) ",
        "\na. ",      # Sub-letters
        "\nb. ",
        "\nc. ",      # Sub-letters
        "\nd. ",
        "\ne. ",      # Sub-letters
        "\nA. ",
        "\nB. ",      # Sub-letters
        "\nC. ",
        "\nD. ",
        "\nE. ",
        "\n- ",      # Bullet points
        "\n\n",      # Paragraphs
        ". ",        # Sentences
        " "          # Words (absolute fallback)
    ]
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=legal_separators,
        length_function=length_fn,
    )
    chunks = text_splitter.split_text(text)
    return chunks

