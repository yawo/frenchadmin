"""Semantic fallback resolver.

Last resort after deterministic + fuzzy fail.
- Embeds context window around unresolved mention
- Scoped cosine search over legi embeddings using <=> (cosine distance)
- Strict acceptance thresholds
"""

import re
from datetime import date
from typing import Optional

from config import EMBEDDING_MODEL, get_logger
from crossreference.alias_detector import CODE_FAMILY_MAP
from database.database_manage import get_connection
from utils import format_model_name, generate_embeddings_with_retry

logger = get_logger(__name__)

SEMANTIC_THRESHOLD_WITH_ALIAS = 0.75
SEMANTIC_THRESHOLD_NO_ALIAS = 0.85
_MODEL_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]+$")
_TAX_CORE_PARENT_IDS = sorted({
    parent_id
    for family_name in ("CGI", "LPF", "CIBS")
    for parent_id in CODE_FAMILY_MAP.get(family_name, {}).get("parent_text_ids", [])
})
_EMBEDDING_COLUMN_AVAILABILITY: dict[str, bool] = {}
_MISSING_COLUMN_WARNED: set[str] = set()


def _embedding_column_available(model_column_suffix: str) -> bool:
    """Check if legi has embeddings_<model_column_suffix> column."""
    if model_column_suffix in _EMBEDDING_COLUMN_AVAILABILITY:
        return _EMBEDDING_COLUMN_AVAILABILITY[model_column_suffix]

    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT EXISTS (
                    SELECT 1
                    FROM information_schema.columns
                    WHERE table_name = 'legi'
                      AND column_name = %s
                )
                """,
                (f"embeddings_{model_column_suffix}",),
            )
            available = bool(cursor.fetchone()[0])
    except Exception as exc:
        logger.warning(
            "Semantic resolve: failed to inspect embedding column embeddings_%s: %s",
            model_column_suffix,
            exc,
        )
        # Do not cache introspection failures as permanent negatives.
        return False

    _EMBEDDING_COLUMN_AVAILABILITY[model_column_suffix] = available
    return available


def semantic_resolve(
    context_window: str,
    source_date: date,
    detected_parents: list[str] = None,
    has_code_alias: bool = False,
    model: str = EMBEDDING_MODEL,
) -> Optional[dict]:
    """Semantic search over LEGI for unresolved mention.

    Returns:
        best matching legi.doc_id row with cosine_similarity score
    """
    if not context_window.strip():
        return None

    # Embed the context window
    try:
        embedding = generate_embeddings_with_retry(
            data=context_window, model=model
        )[0]
    except Exception as e:
        logger.warning(f"Semantic resolve: embedding failed: {e}")
        return None

    sanitized = format_model_name(model)
    if not _MODEL_NAME_RE.match(sanitized):
        raise ValueError(f"Invalid model name for SQL interpolation: {model}")
    if not _embedding_column_available(sanitized):
        if sanitized not in _MISSING_COLUMN_WARNED:
            logger.warning(
                "Semantic resolve: missing LEGI embedding column embeddings_%s; skipping semantic fallback",
                sanitized,
            )
            _MISSING_COLUMN_WARNED.add(sanitized)
        return None
    col = f'"embeddings_{sanitized}"'

    with get_connection() as conn:
        cursor = conn.cursor()

        # Build scoped query:
        # - explicit alias scope: detected parent_text_ids
        # - no alias: tax-core parent_text_ids only
        if detected_parents:
            scope_clause = "AND l.category = ANY(%s)"
            scope_params = [detected_parents]
        else:
            scope_clause = "AND l.category = ANY(%s)"
            scope_params = [_TAX_CORE_PARENT_IDS]

        query = f"""
            WITH ranked AS (
                SELECT
                    l.doc_id,
                    l.category AS parent_text_id,
                    l.number AS article_number,
                    l.start_date::date AS start_date,
                    l.end_date::date AS end_date,
                    1 - (l.{col} <=> %s::vector) AS cosine_similarity,
                    ROW_NUMBER() OVER (
                        PARTITION BY l.doc_id
                        ORDER BY l.{col} <=> %s::vector
                    ) AS rn
                FROM legi l
                WHERE l.start_date::date <= %s
                  AND l.end_date::date >= %s
                  AND l.{col} IS NOT NULL
                  {scope_clause}
            )
            SELECT
                doc_id,
                parent_text_id,
                article_number,
                start_date,
                end_date,
                cosine_similarity
            FROM ranked
            WHERE rn = 1
            ORDER BY cosine_similarity DESC
            LIMIT 10
        """

        params = [embedding, embedding, source_date, source_date] + scope_params
        try:
            cursor.execute(query, params)
            rows = cursor.fetchall()
        except Exception as exc:
            logger.warning(f"Semantic resolve: SQL query failed: {exc}")
            return None

    if not rows:
        return None

    # Check acceptance threshold
    best = rows[0]
    score = best[5]
    if score is None:
        logger.debug("Semantic reject: null cosine_similarity score")
        return None

    # With explicit code alias, trust lower scores; without, require higher confidence
    min_score = SEMANTIC_THRESHOLD_WITH_ALIAS if has_code_alias else SEMANTIC_THRESHOLD_NO_ALIAS

    if score < min_score:
        logger.debug(
            f"Semantic reject: score {score:.3f} < {min_score} "
            f"(alias_detected={has_code_alias}) for context '{context_window[:80]}...'"
        )
        return None

    return {
        "target_legi_doc_id": best[0],
        "target_parent_text_id": best[1],
        "target_article_number": best[2],
        "target_start_date": best[3],
        "target_end_date": best[4],
        "cosine_similarity": score,
    }
