"""Unit tests for the resolver's ambiguity tie-breaker.

`_resolve_ambiguity` receives catalog rows shaped as
``(legi_doc_id, parent_text_id, article_number, code_family, start, end)``.
"""

from datetime import date

from crossreference.resolver import _resolve_ambiguity


def _row(doc, fam):
    return (doc, "LEGITEXT0", "1", fam, date(2000, 1, 1), date(2099, 1, 1))


def test_explicit_family_narrows_to_single_row():
    rows = [_row("A", "CGI"), _row("B", "LPF")]
    result = _resolve_ambiguity(rows, detected_family="CGI", use_loose=False)
    assert result["target_legi_doc_id"] == "A"


def test_explicit_family_with_zero_matches_falls_back_to_all_rows():
    rows = [_row("A", "CIBS"), _row("B", "OTHER_CODE")]
    result = _resolve_ambiguity(
        rows, detected_family="CGI", use_loose=False
    )
    # No CGI rows -> filter does not apply -> two rows remain -> reject.
    assert result is None


def test_no_family_prefers_best_tier_when_singleton():
    rows = [_row("A", "CGI"), _row("B", "OTHER_CODE")]
    result = _resolve_ambiguity(rows, detected_family=None, use_loose=False)
    assert result["target_legi_doc_id"] == "A"


def test_no_family_rejects_when_tier_tie():
    rows = [_row("A", "CGI"), _row("B", "CGI")]
    result = _resolve_ambiguity(rows, detected_family=None, use_loose=False)
    assert result is None


def test_other_code_only_tier_returns_singleton():
    rows = [_row("A", "OTHER_CODE")]
    result = _resolve_ambiguity(rows, detected_family=None, use_loose=False)
    assert result["target_legi_doc_id"] == "A"
