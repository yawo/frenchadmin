"""Restricted fuzzy fallback using rapidfuzz.

Only runs after deterministic steps fail.
- Searches inside scoped candidate set
- Minimum score: 96
- Rejects if top-2 delta < 2
"""

from datetime import date
from typing import Optional

try:
    from rapidfuzz import process as rf_process
    HAS_RAPIDFUZZ = True
except ImportError:
    HAS_RAPIDFUZZ = False

from config import get_logger
from database.database_manage import get_connection

logger = get_logger(__name__)

FUZZY_MIN_SCORE = 96
FUZZY_MIN_DELTA = 2


def fuzzy_resolve(
    normalized_number: str,
    source_date: date,
    detected_parents: list[str] = None,
    detected_family: str = None,
) -> Optional[dict]:
    """Fuzzy scoped resolution after deterministic failure.

    Returns same shape as deterministic resolver.
    """
    if not HAS_RAPIDFUZZ:
        logger.debug("rapidfuzz not installed, skipping fuzzy resolution")
        return None

    # Build scoped candidate set
    candidates = _get_scoped_candidates(
        source_date,
        detected_parents or [],
        detected_family=detected_family,
    )
    if not candidates:
        return None

    # Fuzzy match against normalized_number
    result = rf_process.extractOne(
        normalized_number,
        candidates,
        score_cutoff=FUZZY_MIN_SCORE,
    )
    if not result:
        return None

    match_target, score, match_idx = result

    # Check top-2 delta: single result must have margin, otherwise ambiguous
    all_results = rf_process.extract(
        normalized_number,
        candidates,
        score_cutoff=FUZZY_MIN_SCORE,
        limit=2,
    )
    if len(all_results) == 1:
        # Single match: require high confidence margin above threshold
        if score < FUZZY_MIN_SCORE + 3:
            logger.debug(
                f"Fuzzy reject: single match score {score:.1f} lacks margin "
                f"(threshold {FUZZY_MIN_SCORE}+3) for '{normalized_number}'"
            )
            return None
    elif len(all_results) >= 2:
        delta = all_results[0][1] - all_results[1][1]
        if delta < FUZZY_MIN_DELTA:
            logger.debug(
                f"Fuzzy reject: top-2 delta {delta:.1f} < {FUZZY_MIN_DELTA} "
                f"for '{normalized_number}'"
            )
            return None

    # Reject if fuzzy match changes numeric+alpha structure too much
    if _structure_changed(normalized_number, match_target):
        return None

    # Look up the full row using the fuzzy-matched normalized_number
    # Check both columns since fuzzy may have matched loose or strict
    with get_connection() as conn:
        cursor = conn.cursor()

        if detected_parents:
            scope_clause = "AND parent_text_id = ANY(%s)"
            params = (
                match_target,
                match_target,
                source_date,
                source_date,
                detected_parents,
            )
        elif detected_family:
            scope_clause = "AND code_family = %s"
            params = (
                match_target,
                match_target,
                source_date,
                source_date,
                detected_family,
            )
        else:
            from crossreference.resolver import TAX_CORE_FAMILIES

            scope_clause = "AND code_family = ANY(%s)"
            params = (
                match_target,
                match_target,
                source_date,
                source_date,
                list(TAX_CORE_FAMILIES),
            )

        cursor.execute(
            f"""
            SELECT
                legi_doc_id,
                parent_text_id,
                article_number,
                code_family,
                start_date,
                end_date
            FROM legi_reference_catalog
            WHERE (normalized_number = %s OR normalized_number_loose = %s)
              AND start_date <= %s
              AND end_date >= %s
              {scope_clause}
            """,
            params,
        )
        rows = cursor.fetchall()

    if len(rows) != 1:
        return None
    row = rows[0]

    return {
        "target_legi_doc_id": row[0],
        "target_parent_text_id": row[1],
        "target_article_number": row[2],
        "target_start_date": row[4],
        "target_end_date": row[5],
    }


def _get_scoped_candidates(
    source_date: date,
    detected_parents: list[str],
    detected_family: str = None,
) -> list[str]:
    """Get scoped normalized_number candidates from catalog."""
    from crossreference.resolver import TAX_CORE_FAMILIES

    with get_connection() as conn:
        cursor = conn.cursor()

        if detected_parents:
            cursor.execute("""
                SELECT DISTINCT normalized_number, normalized_number_loose
                FROM legi_reference_catalog
                WHERE start_date <= %s
                  AND end_date >= %s
                  AND parent_text_id = ANY(%s)
            """, (source_date, source_date, detected_parents))
        elif detected_family:
            cursor.execute(
                """
                SELECT DISTINCT normalized_number, normalized_number_loose
                FROM legi_reference_catalog
                WHERE start_date <= %s
                  AND end_date >= %s
                  AND code_family = %s
                """,
                (source_date, source_date, detected_family),
            )
        else:
            # Restrict to tax core families only
            cursor.execute("""
                SELECT DISTINCT normalized_number, normalized_number_loose
                FROM legi_reference_catalog
                WHERE start_date <= %s
                  AND end_date >= %s
                  AND code_family = ANY(%s)
            """, (source_date, source_date, list(TAX_CORE_FAMILIES)))
        candidates = set()
        for normalized, normalized_loose in cursor.fetchall():
            if normalized:
                candidates.add(normalized)
            if normalized_loose:
                candidates.add(normalized_loose)
        return sorted(candidates)


def _structure_changed(original: str, matched: str) -> bool:
    """Reject if fuzzy match changes both numeric and alpha structure."""
    import re
    # Extract numeric parts
    orig_nums = re.findall(r"\d+", original)
    match_nums = re.findall(r"\d+", matched)
    # Extract alpha parts
    orig_alpha = re.findall(r"[A-Z]+", original)
    match_alpha = re.findall(r"[A-Z]+", matched)

    # Reject when numeric sequence differs (e.g., 123 vs 124)
    if orig_nums != match_nums:
        return True
    # Reject if alpha prefixes differ (e.g., L vs R)
    if orig_alpha and match_alpha and orig_alpha[0] != match_alpha[0]:
        return True
    return False
