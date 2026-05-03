"""Shared regex patterns used by extractor and normalizer.

Centralizing these guarantees extractor and normalizer agree on what
constitutes an article token and on which prepositions introduce the
code-name tail.

IGNORECASE is applied globally, so the ``L.O.`` / ``[LRDA]`` prefix and the
French ordinal alternation (bis, ter, quater, ...) accept any case. The
letter-suffix tail ``[A-Z]{1,3}`` is wrapped in ``(?-i:...)`` to stay strictly
uppercase; this prevents lowercase French words (``du``, ``de``, ``cod``,
``liv``, ``et``) from being absorbed as fake suffixes.
"""

import re


ARTICLE_TOKEN_RE = re.compile(
    r"""
    (?:
        (?:L\.O\.|[LRDA])(?:\*)?\s*[-.]?\s*\d+(?:-\d+)*
        (?:\s+(?-i:[A-Z]{1,3}))*
        (?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies|undecies|duodecies|terdecies|quaterdecies|quindecies|sexdecies|septdecies|octodecies|novodecies|vicies)|(?:er|ère|ers))?
        (?:\s+(?-i:[A-Z]{1,3}))*
        |
        \d+(?:-\d+)*
        (?:\s+(?-i:[A-Z]{1,3}))*
        (?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies|undecies|duodecies|terdecies|quaterdecies|quindecies|sexdecies|septdecies|octodecies|novodecies|vicies)|(?:er|ère|ers))?
        (?:\s+(?-i:[A-Z]{1,3}))*
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)


# Prepositions that introduce the code-name tail after an article token.
# Examples:
#   "1745 du code general des impots"
#   "L. 247 du livre des procedures fiscales"
#   "238 de l'annexe II au code general des impots"
#   "L. 1242-1 du code du travail"
# The l['’\s] branch lets us consume "de l'annexe" / "de l annexe" cleanly so
# the stray leading letter l does not leak into the article portion.
PREPOSITION_RE = re.compile(
    r"\s+(?:du|des|de\s+la|de\s+l['’\s]|au|aux|de)\s+",
    re.IGNORECASE,
)


# Anchored form for stripping a trailing code-name clause from a string whose
# leading segment is already an article token (or raw article number). We do
# not anchor on start-of-string because the normalizer applies this after its
# own leading-anchor strip.
TRAILING_CODE_CLAUSE_RE = re.compile(
    r"""
    (?:du|des|de\s+la|de\s+l['’\s]|au|aux|de)
    \s+
    .+$
    """,
    re.IGNORECASE | re.VERBOSE,
)
