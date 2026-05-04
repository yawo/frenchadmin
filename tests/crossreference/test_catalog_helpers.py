"""Unit tests for the pure helpers inside database.cross_reference_manage.

Covers:
- _extract_leading_code_label: must return the leading legal-text label
  ("Code de commerce"), not the full subtitle chain; returns None for
  non-LEGITEXT parents (decrees / laws) and for free-text titles.
"""

import pytest

from database.cross_reference_manage import _extract_leading_code_label


@pytest.mark.parametrize(
    "full_title,title,category,expected",
    [
        # Sample row flagged in the bug report: full_title is a long chain.
        (
            "Code de commerce Partie législative - LIVRE Ier : Du commerce en général. - TITRE II : Des commerçants.",
            "Des personnes tenues à l'immatriculation",
            "LEGITEXT000005634379",
            "Code de commerce",
        ),
        # CGI / CGI annexes must collapse to "Code général des impôts".
        (
            "Code général des impôts - Partie I",
            None,
            "LEGITEXT000006069577",
            "Code général des impôts",
        ),
        (
            "Code général des impôts, annexe II",
            None,
            "LEGITEXT000006069569",
            "Code général des impôts",
        ),
        # LPF is already clean; must be preserved verbatim.
        (
            "Livre des procédures fiscales",
            None,
            "LEGITEXT000006069583",
            "Livre des procédures fiscales",
        ),
        # Non-LEGITEXT parents (decrees, laws) must return None so they never
        # become resolution targets.
        (
            "Loi n° 78-17 du 6 janvier 1978 relative à l'informatique",
            None,
            "JORFTEXT000000886460",
            None,
        ),
        (
            "Décret n°2005-1591 du 19 décembre 2005",
            None,
            "JORFTEXT000000636780",
            None,
        ),
        # Non-Code/non-Livre title must return None even for LEGITEXT parents.
        (
            "Règlement intérieur",
            None,
            "LEGITEXT000000000000",
            None,
        ),
        # If title already starts with "Code", prefer it over full_title.
        (
            "Code général des impôts Partie législative - LIVRE I",
            "Code général des impôts",
            "LEGITEXT000006069577",
            "Code général des impôts",
        ),
    ],
)
def test_extract_leading_code_label(full_title, title, category, expected):
    assert _extract_leading_code_label(full_title, title, category) == expected


def test_extract_leading_code_label_handles_missing_inputs():
    assert _extract_leading_code_label(None, None, None) is None
    assert _extract_leading_code_label("", "", "LEGITEXT123") is None
