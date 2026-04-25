import gc
import math
import os
import time
from collections.abc import Callable

import torch
import xxhash
from langchain.text_splitter import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from transformers import AutoTokenizer

from config import (
    CHUNK_OVERLAP,
    CHUNK_MIN_FILL_RATIO,
    CHUNK_SIZE,
    EMBEDDING_ENCODE_BATCH_SIZE,
    EMBEDDING_MODEL,
    EMBEDDING_BATCH_MAX_SIZE,
    EMBEDDING_RETRY_ATTEMPTS,
    JADE_DATA_FOLDER,
    BOFIP_DATA_FOLDER,
    get_logger,
)

logger = get_logger(__name__)

_tokenizer_cache = {}  # Cache of loaded tokenizers keyed by model name
_embedding_model_cache = {}  # Cache of SentenceTransformer models keyed by (model, device_key)


class NonFiniteEmbeddingError(Exception):
    """Raised when an embedding contains NaN or infinite values."""


def _is_cuda_oom_error(error: Exception) -> bool:
    message = str(error).lower()
    return "cuda out of memory" in message or "cuda error: out of memory" in message


def _has_non_finite_embeddings(embeddings: list[list[float]]) -> bool:
    for vector in embeddings:
        if vector is None:
            return True
        for value in vector:
            if not isinstance(value, (int, float)):
                return True
            if not math.isfinite(float(value)):
                return True
    return False


def _reset_embedding_model(model: str):
    """Drop cached model instance so next call reloads a fresh model."""
    global _embedding_model_cache
    keys_to_drop = [cache_key for cache_key in _embedding_model_cache if cache_key[0] == model]
    for cache_key in keys_to_drop:
        _embedding_model_cache.pop(cache_key, None)
    _release_cuda_memory()


def _release_cuda_memory():
    if not torch.cuda.is_available():
        return
    gc.collect()
    torch.cuda.empty_cache()
    try:
        torch.cuda.ipc_collect()
    except Exception:
        pass


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

    position_ids = auto_model.embeddings.position_ids
    max_position_embeddings = int(auto_model.config.max_position_embeddings)
    sample_size = min(64, max_position_embeddings)
    expected_prefix = torch.arange(sample_size, dtype=torch.long)

    # Models can store this buffer as [max_pos] or [1, max_pos].
    if position_ids.dim() == 2:
        current_prefix = position_ids[0, :sample_size].detach().to("cpu", torch.long)
    else:
        current_prefix = position_ids[:sample_size].detach().to("cpu", torch.long)
    needs_repair = not torch.equal(current_prefix, expected_prefix)
    if not needs_repair:
        return

    repaired_position_ids = torch.arange(
        max_position_embeddings, device=auto_model.device, dtype=torch.long
    )
    if position_ids.dim() == 2:
        repaired_position_ids = repaired_position_ids.unsqueeze(0)

    auto_model.embeddings.register_buffer(
        "position_ids",
        repaired_position_ids,
        persistent=False,
    )
    logger.warning(
        "Repaired corrupted position_ids buffer for '%s' to prevent rotary embedding index errors.",
        model_name,
    )


def _get_embedding_model(
    model: str = EMBEDDING_MODEL,
    device: str | None = None,
) -> SentenceTransformer:
    """
    Returns a cached SentenceTransformer model, downloading it from HuggingFace if needed.

    Args:
        model (str): HuggingFace model identifier. Defaults to EMBEDDING_MODEL.

    Returns:
        SentenceTransformer: The loaded embedding model.
    """
    global _embedding_model_cache
    device_key = (device or "auto").lower()
    cache_key = (model, device_key)
    if cache_key not in _embedding_model_cache:
        logger.info(
            "Loading embedding model '%s' from HuggingFace (device=%s)...",
            model,
            device_key,
        )
        if device and device.lower() == "cpu":
            embedding_model = SentenceTransformer(
                model, trust_remote_code=True, device="cpu"
            )
        else:
            embedding_model = SentenceTransformer(model, trust_remote_code=True)
        _repair_lemone_position_ids(embedding_model=embedding_model, model_name=model)
        _embedding_model_cache[cache_key] = embedding_model
    return _embedding_model_cache[cache_key]


def generate_embeddings(
    data: str | list[str],
    model: str = EMBEDDING_MODEL,
    batch_size: int | None = None,
    device: str | None = None,
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
    embedding_model = _get_embedding_model(model=model, device=device)
    effective_batch_size = max(1, batch_size or EMBEDDING_ENCODE_BATCH_SIZE)
    vectors = embedding_model.encode(
        data,
        batch_size=effective_batch_size,
        convert_to_numpy=True,
        device=device,
    )
    return [v.tolist() if hasattr(v, "tolist") else list(v) for v in vectors]


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
            if _has_non_finite_embeddings(embeddings):
                raise NonFiniteEmbeddingError(
                    "Generated embedding contains non-finite values (NaN/Inf)."
                )
            return embeddings
        except Exception as e:
            is_cuda_oom = _is_cuda_oom_error(e)
            is_non_finite = isinstance(e, NonFiniteEmbeddingError)
            if is_cuda_oom:
                _release_cuda_memory()
            if is_non_finite:
                logger.warning(
                    "Non-finite embeddings detected for batch. Resetting model cache and retrying."
                )
                _reset_embedding_model(model)
            if attempt == attempts - 1:  # If this is the last attempt
                if is_cuda_oom or is_non_finite:
                    logger.warning(
                        "Embedding generation unstable after %s attempts. Falling back to CPU for this batch.",
                        attempts,
                    )
                    try:
                        cpu_embeddings = generate_embeddings(
                            data=data,
                            model=model,
                            batch_size=1,
                            device="cpu",
                        )
                        if _has_non_finite_embeddings(cpu_embeddings):
                            raise NonFiniteEmbeddingError(
                                "CPU fallback produced non-finite embeddings."
                            )
                        return cpu_embeddings
                    except Exception as cpu_error:
                        logger.error(
                            "Error generating embeddings for : %s ... CPU fallback failed: %s",
                            str(data)[:200],
                            cpu_error,
                        )
                        raise cpu_error
                logger.error(
                    f"Error generating embeddings for : {str(data)[:200]} ... Error: {e}. Maximum retries reached ({attempts}). Raising exception."
                )
                raise
            retry_sleep = min(time_sleep, 5) if (is_cuda_oom or is_non_finite) else time_sleep
            logger.error(
                f"Error generating embeddings for : {str(data)[:200]} ... Error: {e}. Retrying in {retry_sleep} seconds (attempt {attempt + 1}/{attempts})"
            )
            time.sleep(retry_sleep)  # Waiting before retrying


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


def _largest_suffix_prefix_overlap(left: str, right: str, max_scan: int = 4096) -> int:
    """Find largest overlap where suffix(left) == prefix(right)."""
    if not left or not right:
        return 0
    max_size = min(len(left), len(right), max_scan)
    for size in range(max_size, 0, -1):
        if left[-size:] == right[:size]:
            return size
    return 0


def _merge_underfilled_chunks(
    chunks: list[str],
    chunk_size: int,
    length_fn: Callable[[str], int],
    min_fill_ratio: float,
) -> list[str]:
    """Merge chunks that are too small due to early separator splits."""
    if len(chunks) < 2:
        return chunks

    min_fill = max(1, int(chunk_size * min_fill_ratio))
    working = list(chunks)
    merged: list[str] = []
    idx = 0

    while idx < len(working):
        current = working[idx]
        current_size = length_fn(current)

        if current_size < min_fill and idx + 1 < len(working):
            next_chunk = working[idx + 1]
            overlap_size = _largest_suffix_prefix_overlap(current, next_chunk)
            candidate = current + next_chunk[overlap_size:]
            if length_fn(candidate) <= chunk_size:
                working[idx + 1] = candidate
                idx += 1
                continue

        merged.append(current)
        idx += 1

    # Final trailing small chunk can still happen after first pass.
    if len(merged) >= 2 and length_fn(merged[-1]) < min_fill:
        overlap_size = _largest_suffix_prefix_overlap(merged[-2], merged[-1])
        candidate = merged[-2] + merged[-1][overlap_size:]
        if length_fn(candidate) <= chunk_size:
            merged[-2] = candidate
            merged.pop()

    return merged



def make_chunks(
    text: str,
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
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
        keep_separator="end",
        length_function=length_fn,
    )
    chunks = text_splitter.split_text(text)
    return _merge_underfilled_chunks(
        chunks=chunks,
        chunk_size=chunk_size,
        length_fn=length_fn,
        min_fill_ratio=CHUNK_MIN_FILL_RATIO,
    )
