"""Deterministic resolver: article number + temporal filtering -> legi.doc_id.

Cascade:
A: exact normalized_number + source_date + optional parent_text_ids
B: exact loose key (if A fails)
C: family-prior deterministic without explicit code (if B fails)
D: fuzzy scoped (if C fails)
E: semantic scoped (if D fails)
"""

import re
from datetime import date
from typing import Optional

from config import EMBEDDING_MODEL
from database.database_manage import get_connection
from crossreference.normalizer import normalize_article_number, loose_normalized_number
from crossreference.alias_detector import infer_code_family
from crossreference.fuzzy_resolver import fuzzy_resolve
from crossreference.semantic_resolver import semantic_resolve

TAX_CORE_FAMILIES = {"CGI", "LPF", "CIBS"}


def resolve_article(
    raw_article_text: str,
    source_date: date,
    context_text: str = "",
    source_type: str = "",
    model: str = EMBEDDING_MODEL,
) -> dict:
    """Resolve one article mention to a versioned legi.doc_id.

    Returns dict with:
        target_legi_doc_id, target_parent_text_id, target_article_number,
        target_start_date, target_end_date,
        resolver_method, confidence, explain
    """
    normalized = normalize_article_number(raw_article_text)
    loose = loose_normalized_number(normalized)

    # Detect code family from context
    detected_family, detected_alias, detected_parents = infer_code_family(context_text)

    explain = {
        "raw_text": raw_article_text,
        "normalized": normalized,
        "loose": loose,
        "detected_code_alias": detected_alias,
        "detected_code_family": detected_family,
        "detected_parent_text_ids": detected_parents,
    }

    # Step A: exact normalized + temporal
    result = _exact_resolve(
        normalized, source_date, detected_parents,
        detected_family=detected_family, use_loose=False
    )
    if result:
        method = (
            "exact_number_and_explicit_code"
            if detected_family
            else "exact_number_and_temporal_unique"
        )
        return {
            **result,
            "resolver_method": method,
            "explain": explain,
        }

    # Step B: loose key
    result = _exact_resolve(
        loose, source_date, detected_parents,
        detected_family=detected_family, use_loose=True
    )
    if result:
        method = "exact_loose_and_explicit_code" if detected_family else "exact_loose"
        return {
            **result,
            "resolver_method": method,
            "explain": explain,
        }

    # Step C: family-prior deterministic without explicit code
    if not detected_family:
        result = _family_prior_resolve(normalized, source_date)
        if result:
            return {
                **result,
                "resolver_method": "exact_number_core_family_only",
                "explain": {**explain, "family_prior_scope": "tax_core"},
            }

    # Step D: fuzzy scoped
    result = fuzzy_resolve(
        normalized,
        source_date,
        detected_parents,
        detected_family=detected_family,
    )
    if result:
        return {
            **result,
            "resolver_method": "fuzzy_scoped",
            "explain": {**explain, "fuzzy_match": True},
        }

    # Step E: semantic fallback
    result = semantic_resolve(
        context_window=context_text,
        source_date=source_date,
        detected_parents=detected_parents,
        has_code_alias=bool(detected_alias),
        model=model,
    )
    if result:
        return {
            "target_legi_doc_id": result["target_legi_doc_id"],
            "target_parent_text_id": result["target_parent_text_id"],
            "target_article_number": result["target_article_number"],
            "target_start_date": result["target_start_date"],
            "target_end_date": result["target_end_date"],
            "resolver_method": "semantic_scoped",
            "explain": {
                **explain,
                "cosine_similarity": result.get("cosine_similarity"),
            },
        }

    # Step F: ambiguity resolver — if multiple candidates survived any step,
    # apply ranked preferences before final rejection
    # (handled inline in _exact_resolve and _family_prior_resolve below)

    # All steps failed
    return {
        "target_legi_doc_id": None,
        "target_parent_text_id": None,
        "target_article_number": None,
        "target_start_date": None,
        "target_end_date": None,
        "resolver_method": "unresolved",
        "confidence": 0.0,
        "explain": explain,
    }


def _exact_resolve(
    normalized_number: str,
    source_date: date,
    detected_parents: list[str],
    detected_family: str = None,
    use_loose: bool = False,
) -> Optional[dict]:
    """Query legi_reference_catalog with exact match.

    Returns:
        Single resolved row dict, or None if 0 or >1 candidates.
    """
    col = "normalized_number_loose" if use_loose else "normalized_number"

    with get_connection() as conn:
        cursor = conn.cursor()

        # Build clause conditionally to avoid array comparison fragility
        if detected_parents:
            scope_clause = "AND parent_text_id = ANY(%s)"
            params = (normalized_number, source_date, source_date, detected_parents)
        elif detected_family:
            scope_clause = "AND code_family = %s"
            params = (normalized_number, source_date, source_date, detected_family)
        else:
            # Never perform article-number-only deterministic matching over full corpus.
            scope_clause = "AND code_family = ANY(%s)"
            params = (normalized_number, source_date, source_date, list(TAX_CORE_FAMILIES))

        cursor.execute(f"""
            SELECT
                legi_doc_id,
                parent_text_id,
                article_number,
                code_family,
                start_date,
                end_date
            FROM legi_reference_catalog
            WHERE {col} = %s
              AND start_date <= %s
              AND end_date >= %s
              {scope_clause}
        """, params)
        rows = cursor.fetchall()

    if len(rows) == 1:
        row = rows[0]
        (legi_doc_id, parent_text_id, article_number, code_family, 
         start_date, end_date) = row
        return {
            "target_legi_doc_id": legi_doc_id,
            "target_parent_text_id": parent_text_id,
            "target_article_number": article_number,
            "target_start_date": start_date,
            "target_end_date": end_date,
        }
    elif len(rows) > 1:
        # Step F: ambiguity resolver — apply ranked preferences
        return _resolve_ambiguity(rows, detected_family, use_loose)
    return None


def _is_generic_numeric_ref(normalized_number: str) -> bool:
    """Check if number is too generic for family-prior resolution."""
    return bool(re.fullmatch(r"\d{1,2}(?:\s+(?:BIS|TER))?", normalized_number))


def _resolve_ambiguity(rows, detected_family: str, use_loose: bool) -> Optional[dict]:
    """Step F: ambiguity resolver.

    Ranked preferences:
    1. prefer exact family match over inferred family
    2. prefer non-loose hit over loose
    3. if tie remains, reject rather than guess
    """
    if detected_family:
        # Prefer rows with matching code_family (code_family is at index 3)
        family_rows = [r for r in rows if r[3] == detected_family]
        if len(family_rows) == 1:
            row = family_rows[0]
            (legi_doc_id, parent_text_id, article_number, code_family, 
             start_date, end_date) = row
            return {
                "target_legi_doc_id": legi_doc_id,
                "target_parent_text_id": parent_text_id,
                "target_article_number": article_number,
                "target_start_date": start_date,
                "target_end_date": end_date,
            }
        elif len(family_rows) > 1:
            rows = family_rows  # narrow to family subset

    if len(rows) == 1:
        row = rows[0]
        (legi_doc_id, parent_text_id, article_number, code_family, 
         start_date, end_date) = row
        return {
            "target_legi_doc_id": legi_doc_id,
            "target_parent_text_id": parent_text_id,
            "target_article_number": article_number,
            "target_start_date": start_date,
            "target_end_date": end_date,
        }

    # Tie remains — reject
    return None


def _family_prior_resolve(
    normalized_number: str,
    source_date: date,
) -> Optional[dict]:
    """Restrict to tax core families, accept only if one candidate remains."""
    if _is_generic_numeric_ref(normalized_number):
        return None

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                legi_doc_id,
                parent_text_id,
                article_number,
                code_family,
                start_date,
                end_date
            FROM legi_reference_catalog
            WHERE normalized_number = %s
              AND start_date <= %s
              AND end_date >= %s
              AND code_family = ANY(%s)
        """, (
            normalized_number,
            source_date,
            source_date,
            list(TAX_CORE_FAMILIES),
        ))
        rows = cursor.fetchall()

    if len(rows) == 1:
        row = rows[0]
        return {
            "target_legi_doc_id": row[0],
            "target_parent_text_id": row[1],
            "target_article_number": row[2],
            "target_start_date": row[4],
            "target_end_date": row[5],
        }
    elif len(rows) > 1:
        # Step F: try to disambiguate within tax core
        return _resolve_ambiguity(rows, detected_family=None, use_loose=False)
    return None
