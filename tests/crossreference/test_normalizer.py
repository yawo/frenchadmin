"""Unit tests for crossreference.normalizer.

These are the normalization contracts that the resolver relies on: mentions
extracted from JADE/BOFIP must normalize to the same keys the catalog uses.
"""

import pytest

from crossreference.normalizer import (
    loose_normalized_number,
    normalize_article_number,
)


@pytest.mark.parametrize(
    "raw,expected",
    [
        # Base forms from the CROSSREFERENCE.md examples.
        ("art. 150-0 A", "150-0 A"),
        ("article R* 196-1", "R*196-1"),
        ("article 1012 ter A", "1012 TER A"),
        ("article 01 bis", "1 BIS"),
        # Rich matched_text carrying a trailing code-name clause must collapse
        # to the bare article number.
        ("1745 du code general des impots", "1745"),
        ("L. 247 du livre des procedures fiscales", "L247"),
        ("238 de l'annexe II au code general des impots", "238"),
        ("117 du code general des impots", "117"),
        ("L. 53 du livre des procedures fiscales", "L53"),
        # Code-name tail on suffixed / starred numbers must be stripped.
        ("article R* 196-1 du CGI", "R*196-1"),
        ("1012 ter A du code general des impots", "1012 TER A"),
        ("150-0 A du CGI", "150-0 A"),
        ("article 10 bis du CGI", "10 BIS"),
        ("L. 1242-1 du code du travail", "L1242-1"),
        # Catalog-side inputs (LEGI.number) must round-trip.
        ("L123-3", "L123-3"),
        ("L123-9-1", "L123-9-1"),
        ("R*196-1", "R*196-1"),
        ("42 septies", "42 SEPTIES"),
        # L.O. prefix gap collapse.
        ("L.O. 234-5", "L.O.234-5"),
        ("article L.O. 111-9", "L.O.111-9"),
        # Alpha-hyphen-digit patterns (§7.5: "10 G-0 bis" structure).
        ("article 10 G-0 bis", "10 G-0 BIS"),
        ("article 10 B-0 du CGI", "10 B-0"),
        ("article 1 A-0 bis du CGI", "1 A-0 BIS"),
        # No-tail inputs stay untouched.
        ("article 1", "1"),
    ],
)
def test_normalize_article_number(raw, expected):
    assert normalize_article_number(raw) == expected


def test_normalize_article_number_empty():
    assert normalize_article_number("") == ""
    assert normalize_article_number("   ") == ""


def test_normalize_article_number_requires_digit():
    assert normalize_article_number("article CGI") == ""


def test_loose_key_removes_spaces_only():
    assert loose_normalized_number("150-0 A") == "150-0A"
    assert loose_normalized_number("R*196-1") == "R*196-1"
    assert loose_normalized_number("1012 TER A") == "1012TERA"
    assert loose_normalized_number("L247") == "L247"
