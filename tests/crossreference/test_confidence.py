"""Unit tests for crossreference.confidence."""

import pytest

from crossreference.confidence import score_confidence


@pytest.mark.parametrize("method,expected_base", [
    ("exact_number_and_explicit_code", 0.99),
    ("exact_number_and_temporal_unique", 0.96),
    ("exact_loose_and_explicit_code", 0.92),
    ("exact_loose", 0.85),
    ("exact_number_core_family_only", 0.84),
    ("fuzzy_scoped", 0.78),
    ("semantic_scoped", 0.65),
    ("unresolved", 0.0),
])
def test_base_scores(method, expected_base):
    conf, _ = score_confidence(method, "jade")
    # No alias -> -0.05 penalty, clamped to [0, 1]
    expected = max(0.0, min(1.0, expected_base - 0.05))
    assert abs(conf - expected) < 0.001


def test_vu_boost_jade_only():
    conf_jade, _ = score_confidence(
        "exact_number_and_explicit_code", "jade",
        detected_code_alias="cgi", mention_in_vu_section=True,
    )
    conf_bofip, _ = score_confidence(
        "exact_number_and_explicit_code", "bofip",
        detected_code_alias="cgi", mention_in_vu_section=True,
    )
    assert conf_jade == 1.0  # 0.99 + 0.05 clamped
    assert conf_bofip == 0.99  # bofip doesn't get VU boost


def test_generic_penalty_stacks():
    conf, _ = score_confidence(
        "semantic_scoped", "jade",
        detected_code_alias=None, is_generic=True,
    )
    # 0.65 - 0.05 (no alias) - 0.10 (generic) = 0.50
    assert abs(conf - 0.50) < 0.001
    assert conf < 0.55  # Below acceptance threshold


def test_clamp_to_0_1():
    conf, _ = score_confidence(
        "unresolved", "jade", is_generic=True,
    )
    assert conf == 0.0  # Cannot go below 0


def test_repeated_in_chunks_boost():
    # Use a method where clamping won't interfere
    conf_no, _ = score_confidence(
        "exact_loose", "jade",
        detected_code_alias="cgi", repeated_in_chunks=False,
    )
    conf_yes, _ = score_confidence(
        "exact_loose", "jade",
        detected_code_alias="cgi", repeated_in_chunks=True,
    )
    assert abs(conf_yes - conf_no - 0.05) < 0.001


def test_semantic_similarity_tiered_boost():
    conf_75, _ = score_confidence(
        "semantic_scoped", "jade",
        detected_code_alias="cgi", semantic_similarity=0.75,
    )
    conf_92, _ = score_confidence(
        "semantic_scoped", "jade",
        detected_code_alias="cgi", semantic_similarity=0.92,
    )
    # 0.65 + 0.15 = 0.80 for 0.75 similarity
    assert abs(conf_75 - 0.80) < 0.001
    # 0.65 + 0.35 = 1.0 for 0.92 similarity (clamped)
    assert conf_92 == 1.0


def test_no_alias_penalty_explain():
    _, explain = score_confidence(
        "exact_number_and_explicit_code", "jade",
        detected_code_alias=None,
    )
    assert explain["no_alias_penalty"] == -0.05
