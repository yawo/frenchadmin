"""Code family alias detection.

Maps known code labels/abbreviations to parent_text_id families.
"""

import re
import time
from collections import defaultdict

from unidecode import unidecode

from config import get_logger

logger = get_logger(__name__)

CODE_FAMILY_MAP = {
    "CGI": {
        "parent_text_ids": [
            "LEGITEXT000006069577",  # Code general des impots
            "LEGITEXT000006069568",  # Code general des impots, annexe I
            "LEGITEXT000006069569",  # Code general des impots, annexe II
            "LEGITEXT000006069574",  # Code general des impots, annexe III
            "LEGITEXT000006069576",  # Code general des impots, annexe IV
        ],
        "aliases": [
            "cgi",
            "code general des impots",
            "code des impots",
            "annexe i au cgi",
            "annexe ii au cgi",
            "annexe iii au cgi",
            "annexe iv au cgi",
        ],
    },
    "LPF": {
        "parent_text_ids": ["LEGITEXT000006069583"],
        "aliases": [
            "lpf",
            "livre des procedures fiscales",
        ],
    },
    "CIBS": {
        "parent_text_ids": ["LEGITEXT000044595989"],
        "aliases": [
            "cibs",
            "code des impositions sur les biens et services",
        ],
    },
}

# Punctuation pattern for stripping
_PUNCT_RE = re.compile(r"[^\w\s]")
_EXTENDED_ALIAS_CACHE_TTL_SEC = 900
_EXTENDED_ALIAS_CACHE = {
    "loaded_at": 0.0,
    "aliases": {},
}


def _normalize_for_alias(text: str) -> str:
    """Normalize text for alias matching: lowercase, unidecode, strip punctuation."""
    t = unidecode(text.lower())
    t = _PUNCT_RE.sub("", t).strip()
    t = re.sub(r"\s+", " ", t).strip()
    return t


# Reverse alias lookup: normalized alias -> (family_name, raw_alias, parent_text_ids)
_ALIAS_TO_FAMILY = {}
for _fam_name, _fam_info in CODE_FAMILY_MAP.items():
    for _alias in _fam_info["aliases"]:
        _norm_alias = _normalize_for_alias(_alias)
        _ALIAS_TO_FAMILY[_norm_alias] = (
            _fam_name,
            _alias,
            _fam_info["parent_text_ids"],
        )
_SORTED_CORE_ALIASES = sorted(_ALIAS_TO_FAMILY.keys(), key=len, reverse=True)


def _alias_in_text(alias: str, normalized_text: str) -> bool:
    return re.search(rf"\b{re.escape(alias)}\b", normalized_text) is not None


def _load_extended_aliases_from_catalog() -> dict[str, list[str]]:
    """Load non-core code aliases from legi_reference_catalog.code_label."""
    try:
        from database.database_manage import get_connection
    except Exception as exc:
        logger.warning(
            "Extended alias detection disabled: cannot import database connection helper: %s",
            exc,
        )
        return {}

    alias_map = defaultdict(set)
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT DISTINCT parent_text_id, code_label
                FROM legi_reference_catalog
                WHERE code_label IS NOT NULL
                  AND code_label <> ''
                """
            )
            rows = cursor.fetchall()
    except Exception as exc:
        logger.warning(
            "Extended alias detection disabled: failed to query legi_reference_catalog: %s",
            exc,
        )
        return {}

    core_parent_ids = {
        parent_id
        for family in CODE_FAMILY_MAP.values()
        for parent_id in family.get("parent_text_ids", [])
    }

    for parent_text_id, code_label in rows:
        if not parent_text_id or not code_label:
            continue
        if parent_text_id in core_parent_ids:
            continue
        normalized_label = _normalize_for_alias(code_label)
        if not normalized_label:
            continue
        alias_map[normalized_label].add(parent_text_id)

    return {alias: sorted(parent_ids) for alias, parent_ids in alias_map.items()}


def _get_extended_aliases() -> dict[str, list[str]]:
    now = time.time()
    if now - _EXTENDED_ALIAS_CACHE["loaded_at"] <= _EXTENDED_ALIAS_CACHE_TTL_SEC:
        return _EXTENDED_ALIAS_CACHE["aliases"]
    aliases = _load_extended_aliases_from_catalog()
    _EXTENDED_ALIAS_CACHE["aliases"] = aliases
    _EXTENDED_ALIAS_CACHE["loaded_at"] = now
    return aliases


def invalidate_extended_alias_cache():
    """Invalidate cached extended aliases (used after catalog refresh)."""
    _EXTENDED_ALIAS_CACHE["loaded_at"] = 0.0
    _EXTENDED_ALIAS_CACHE["aliases"] = {}


def infer_code_family(context_text: str) -> tuple:
    """Detect code family from context text.

    Returns:
        (family_name, matched_alias, list_of_parent_text_ids) or (None, None, [])
    """
    if not context_text:
        return None, None, []

    normalized = _normalize_for_alias(context_text)

    for norm_alias in _SORTED_CORE_ALIASES:
        if _alias_in_text(norm_alias, normalized):
            family, raw_alias, parent_ids = _ALIAS_TO_FAMILY[norm_alias]
            return family, raw_alias, parent_ids

    extended_aliases = _get_extended_aliases()
    for alias in sorted(extended_aliases.keys(), key=len, reverse=True):
        if _alias_in_text(alias, normalized):
            return "OTHER_CODE", alias, extended_aliases[alias]

    return None, None, []


def get_family_aliases(family: str) -> list[str]:
    """Get all aliases for a known family."""
    info = CODE_FAMILY_MAP.get(family)
    if not info:
        return []
    return list(info["aliases"])


def extract_code_family_from_mention(mention_text: str) -> str | None:
    """Extract code family from mention text.
    
    E.g. "1745 du code général des impôts" -> "CGI"
         "L. 247 du livre des procédures fiscales" -> "LPF"
    
    Returns:
        family name (e.g. "CGI", "LPF") or None if not detected.
    """
    if not mention_text:
        return None
    
    normalized = _normalize_for_alias(mention_text)
    
    # Try core aliases first (longest first for specificity)
    for norm_alias in _SORTED_CORE_ALIASES:
        if _alias_in_text(norm_alias, normalized):
            family, _, _ = _ALIAS_TO_FAMILY[norm_alias]
            return family
    
    # Try extended aliases
    extended_aliases = _get_extended_aliases()
    for alias in sorted(extended_aliases.keys(), key=len, reverse=True):
        if _alias_in_text(alias, normalized):
            return "OTHER_CODE"
    
    return None
