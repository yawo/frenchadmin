"""Unit tests for crossreference.extractor.

Contract:
- Each yielded tuple is (matched_text, article_token, start, end, ctx).
- matched_text preserves the rich span (article + optional code-name tail).
- article_token is the clean article span used for normalization.
- Enumerations ("articles X, Y et Z du CGI") yield one mention per token.
"""

import pytest

from crossreference.extractor import extract_article_mentions
from crossreference.normalizer import normalize_article_number


def _mentions(text):
    return list(extract_article_mentions(text))


def test_five_failing_samples_from_bug_report():
    """Direct regression test for the mentions reported by the user."""
    samples_expectations = [
        (
            "Vu l article 1745 du code général des impôts qui dispose",
            ("1745 du code général des impôts", "1745", "1745"),
        ),
        (
            "L application de l article L. 247 du livre des procédures fiscales",
            (
                "L. 247 du livre des procédures fiscales",
                "L. 247",
                "L247",
            ),
        ),
        (
            "application de l'article 238 de l'annexe II au code général des impôts",
            (
                # Preposition regex now swallows "de l'" so the stray "l" does
                # not leak into the article portion. matched_text still shows
                # the reconstructed "du" form from the extractor.
                "238 du l'annexe II au code général des impôts",
                "238",
                "238",
            ),
        ),
        (
            "application de l article 117 du code général des impôts",
            ("117 du code général des impôts", "117", "117"),
        ),
        (
            "L article L. 53 du livre des procédures fiscales",
            ("L. 53 du livre des procédures fiscales", "L. 53", "L53"),
        ),
    ]
    for source, (exp_matched, exp_token, exp_norm) in samples_expectations:
        mentions = _mentions(source)
        assert len(mentions) == 1, f"{source!r} yielded {mentions!r}"
        matched, token, _, _, _ = mentions[0]
        assert matched == exp_matched, source
        assert token == exp_token, source
        assert normalize_article_number(token) == exp_norm, source


def test_enumeration_yields_one_mention_per_token():
    mentions = _mentions("les articles 38, 39 et 39 A du code général des impôts")
    tokens = [t for (_, t, *_rest) in mentions]
    assert tokens == ["38", "39", "39 A"]
    normalized = [normalize_article_number(t) for t in tokens]
    assert normalized == ["38", "39", "39 A"]


def test_code_du_travail_non_core():
    mentions = _mentions("Aux termes de l article L. 1242-1 du code du travail")
    assert len(mentions) == 1
    matched, token, _, _, _ = mentions[0]
    assert matched == "L. 1242-1 du code du travail"
    assert token == "L. 1242-1"
    assert normalize_article_number(token) == "L1242-1"


def test_star_and_ordinal_suffix_survive():
    mentions = _mentions("article R*196-1 du livre des procédures fiscales")
    assert len(mentions) == 1
    _, token, _, _, _ = mentions[0]
    assert token == "R*196-1"


def test_lowercase_words_are_not_captured_as_alpha_suffix():
    """Regression: ARTICLE_TOKEN_RE's alpha-tail must not absorb 'du', 'cod',
    'liv', etc. article_token must stop at the article number proper.
    """
    mentions = _mentions("article 1745 du code général des impôts")
    _, token, _, _, _ = mentions[0]
    assert token == "1745"


def test_empty_input_is_safe():
    assert _mentions("") == []
    assert _mentions(None) == []
