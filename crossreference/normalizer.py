"""Article number normalization.

Two forms:
- primary: exact deterministic lookup (keep hyphens, *, suffixes)
- loose: restricted fallback (remove spaces only, keep hyphens and *)
"""

import re
import unicodedata

# Strip leading anchors
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

# Normalize spaces around hyphens and prefix-star
_SPACE_HYPHEN_RE = re.compile(r"\s*-\s*")
_PREFIX_STAR_SPACE_RE = re.compile(r"([LRDA])\s*\*\s*")
_MULTI_SPACE_RE = re.compile(r"\s+")


def normalize_article_number(raw: str) -> str:
    """Primary normalized form for exact deterministic lookup.

    Examples:
        normalize_article_number("art. 150-0 A") == "150-0 A"
        normalize_article_number("article R* 196-1") == "R*196-1"
        normalize_article_number("article 1012 ter A") == "1012 TER A"
        normalize_article_number("article 01 bis") == "1 BIS"
    """
    if not raw:
        return ""

    text = raw.strip()

    # Strip leading anchors
    text = _ANCHOR_RE.sub("", text).strip()

    # Normalize unicode spaces
    text = unicodedata.normalize("NFKC", text)

    # Normalize repeated whitespace to one space
    text = _MULTI_SPACE_RE.sub(" ", text)

    # Remove spaces around hyphens
    text = _SPACE_HYPHEN_RE.sub("-", text)

    # Remove spaces between prefix and star: R* 196-1 -> R*196-1
    text = _PREFIX_STAR_SPACE_RE.sub(r"\1*", text)

    # Uppercase
    text = text.upper()

    # Remove leading zeros from purely numeric leading part (but keep hyphen structure)
    # e.g. "01 BIS" -> "1 BIS"
    text = re.sub(r"\b0+(\d)", r"\1", text)

    result = text.strip()

    # Guard: must contain at least one digit to be a valid article number
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
