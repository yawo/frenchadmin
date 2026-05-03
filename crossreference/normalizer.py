"""Article number normalization.

Two forms:
- primary: exact deterministic lookup (keep hyphens, *, suffixes)
- loose: restricted fallback (remove spaces only, keep hyphens and *)

Normalization also strips the trailing "du/de/au <code-name>" clause so that
keys derived from a rich matched_text such as "1745 du code general des impots"
collapse to the catalog key ("1745"). The tail is stripped only when the
portion before the preposition is itself a valid article token, so inputs that
merely happen to contain "de" inside an unrelated phrase remain untouched.
"""

import re
import unicodedata

from crossreference._patterns import ARTICLE_TOKEN_RE, PREPOSITION_RE

_ANCHOR_RE = re.compile(
    r"""
    ^(?:
        article\s+ | articles\s+ | art\.\s* | art\s+ |
        article\s+n°\s* | article\s+n\.\s* |
        n°\s*
    )+
    """,
    re.IGNORECASE | re.VERBOSE,
)

_SPACE_HYPHEN_RE = re.compile(r"\s*-\s*")
_PREFIX_STAR_SPACE_RE = re.compile(r"([LRDA])\s*\*\s*")
# "L. 247" / "L 247" -> "L247". Preserve "L*247" by excluding the star branch.
_LETTER_PREFIX_GAP_RE = re.compile(r"(?i)\b([LRDA])(?!\s*\*)\.?\s+(\d)")
_MULTI_SPACE_RE = re.compile(r"\s+")


def _strip_trailing_code_clause(text: str) -> str:
    """Drop a trailing " du/de/... <code-name>" clause when the prefix is a valid
    article token.

    Returns the untouched input when:
    - no preposition is found,
    - the preposition is found but the prefix does not fully match ARTICLE_TOKEN_RE
      (means the "du/de" belongs to a different phrase, not a code-name hint),
    - the segment after the preposition is purely numeric (defensive guard).
    """
    if not text:
        return text
    for match in PREPOSITION_RE.finditer(text):
        prefix = text[: match.start()].strip()
        tail = text[match.end():].strip()
        if not prefix or not tail:
            continue
        if not ARTICLE_TOKEN_RE.fullmatch(prefix):
            continue
        if re.fullmatch(r"\d+(?:-\d+)*", tail):
            continue
        return prefix
    return text


def normalize_article_number(raw: str) -> str:
    """Primary normalized form for exact deterministic lookup.

    Examples:
        normalize_article_number("art. 150-0 A") == "150-0 A"
        normalize_article_number("article R* 196-1") == "R*196-1"
        normalize_article_number("article 1012 ter A") == "1012 TER A"
        normalize_article_number("article 01 bis") == "1 BIS"
        normalize_article_number("1745 du code general des impots") == "1745"
        normalize_article_number("L. 247 du livre des procedures fiscales") == "L.247"
        normalize_article_number("238 de l'annexe II au code general des impots") == "238"
    """
    if not raw:
        return ""

    text = raw.strip()

    text = _ANCHOR_RE.sub("", text).strip()
    text = unicodedata.normalize("NFKC", text)
    text = _MULTI_SPACE_RE.sub(" ", text)

    text = _strip_trailing_code_clause(text)

    text = _SPACE_HYPHEN_RE.sub("-", text)
    text = _PREFIX_STAR_SPACE_RE.sub(r"\1*", text)
    text = _LETTER_PREFIX_GAP_RE.sub(r"\1\2", text)
    text = text.upper()

    # e.g. "01 BIS" -> "1 BIS" (leading zeros on a pure numeric head)
    text = re.sub(r"\b0+(\d)", r"\1", text)

    result = text.strip()

    if result and not re.search(r"\d", result):
        return ""

    return result


def loose_normalized_number(primary: str) -> str:
    """Loose form: remove spaces only, keep hyphens and *.

    Examples:
        loose_normalized_number("150-0 A") == "150-0A"
        loose_normalized_number("R*196-1") == "R*196-1"
        loose_normalized_number("1012 TER A") == "1012TERA"
    """
    return primary.replace(" ", "")
