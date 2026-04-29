"""Confidence scoring for cross-reference resolution.

Encodes why a link is trustworthy, not just numeric similarity.
"""


def score_confidence(
    resolver_method: str,
    source_type: str,
    detected_code_alias: str = None,
    is_generic: bool = False,
    repeated_in_chunks: bool = False,
    mention_in_vu_section: bool = False,
    semantic_similarity: float | None = None,
) -> tuple[float, dict]:
    """Score confidence based on resolution method and context.

    Returns:
        (confidence, explain_detail)
    """
    explain = {}

    # Base score by method
    base_scores = {
        "exact_number_and_explicit_code": 0.99,
        "exact_number_and_temporal_unique": 0.96,
        "exact_loose_and_explicit_code": 0.92,
        "exact_loose": 0.80,
        "exact_number_core_family_only": 0.84,
        "fuzzy_scoped": 0.74,
        "semantic_scoped": 0.62,
        "unresolved": 0.0,
    }

    confidence = base_scores.get(resolver_method, 0.0)

    # Adjustments
    if source_type == "jade" and mention_in_vu_section:
        confidence += 0.03
        explain["vu_boost"] = 0.03

    if repeated_in_chunks:
        confidence += 0.03
        explain["repeated_boost"] = 0.03

    if resolver_method == "semantic_scoped" and semantic_similarity is not None:
        semantic_boost = 0.0
        if semantic_similarity >= 0.92:
            semantic_boost = 0.30
        elif semantic_similarity >= 0.88:
            semantic_boost = 0.25
        elif semantic_similarity >= 0.84:
            semantic_boost = 0.20
        elif semantic_similarity >= 0.80:
            semantic_boost = 0.15
        elif semantic_similarity >= 0.75:
            semantic_boost = 0.10
        confidence += semantic_boost
        explain["semantic_similarity_boost"] = semantic_boost

    # Only penalize if no explicit code alias was detected in source text
    # (inferred family does not count as "detected alias")
    if detected_code_alias is None:
        confidence -= 0.10
        explain["no_alias_penalty"] = -0.10

    if is_generic:
        confidence -= 0.15
        explain["generic_penalty"] = -0.15

    # Clamp
    confidence = max(0.0, min(1.0, confidence))

    return confidence, explain
