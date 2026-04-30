"""Article token extraction from source text.

Two-step extraction:
1. Find anchor (article/articles/art.)
2. Parse following article token(s) until punctuation or code-name boundary
"""

import re

from crossreference.normalizer import normalize_article_number

# Single article token pattern
# Supports: numeric, numeric-hyphen chains, L/R/D/A prefixes (including L.O.), *, spaced suffixes, ordinal suffixes
ARTICLE_TOKEN_RE = re.compile(
    r"""
    (?:
        (?:L\.O\.|[LRDA])(?:\*)?\s*[-.]?\s*\d+(?:-\d+)*(?:\s+[A-Z]{1,3})*(?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies|undecies|duodecies|terdecies|quaterdecies|quindecies|sexdecies|septdecies|octodecies|novodecies|vicies)|(?:er|ère|ers))?(?:\s+[A-Z]{1,3})*
        |
        \d+(?:-\d+)*(?:\s+[A-Z]{1,3})*(?:\s+(?:bis|ter|quater|quinquies|sexies|septies|octies|nonies|decies|undecies|duodecies|terdecies|quaterdecies|quindecies|sexdecies|septdecies|octodecies|novodecies|vicies)|(?:er|ère|ers))?(?:\s+[A-Z]{1,3})*
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Anchor pattern: article, articles, art.
ANCHOR_RE = re.compile(
    r"(?:article|articles|art\.)\s+",
    re.IGNORECASE,
)

# Separator between enumerated articles: ", ", " et ", " ou "
ENUM_SEP_RE = re.compile(r"\s*(?:,|et|ou)\s+")

# Code-name boundary: known code labels that should stop article extraction
_CODE_BOUNDARY_RE = re.compile(
    r"""\b(?:
        cgi|code\s+general\s+des\s+impots|code\s+des\s+impots|
        annexe\s+[ivx]+?\s+au\s+cgi|
        lpf|livre\s+des\s+procedures\s+fiscales|
        cibs|code\s+des\s+impositions\s+sur\s+les\s+biens\s+et\s+services|
        code\s+du\s+travail|code\s+de\s+la\s+securite\s+sociale|
        code\s+monetaire\s+et\s+financier|code\s+de\s+l['\s]?urbanisme|
        du\s+cgi|du\s+lpf|du\s+cibs
    )\b""",
    re.IGNORECASE | re.VERBOSE,
)


def extract_article_mentions(text: str):
    """Extract all article mentions from text.

    Yields:
        (matched_text, start_pos, end_pos, context_window)

    matched_text: raw extracted article reference(s)
    context_window: ~200 chars around the match
    """
    if not text:
        return

    window_size = 200

    for m in ANCHOR_RE.finditer(text):
        anchor_end = m.end()
        cursor = anchor_end
        mentions = []

        while True:
            token_match = ARTICLE_TOKEN_RE.match(text[cursor:])
            if not token_match:
                break

            raw_start = cursor + token_match.start()
            raw_end = cursor + token_match.end()
            end = raw_end
            matched_raw = text[raw_start:raw_end]
            # ARTICLE_TOKEN_RE can absorb conjunctions like "et"/"ou" as alpha tails.
            # Trim them so enumeration parsing can continue on separators.
            conjunction_match = re.search(r"\s+(et|ou)\s*$", matched_raw, re.IGNORECASE)
            if conjunction_match:
                matched_raw = matched_raw[:conjunction_match.start()]
                raw_end = raw_start + len(matched_raw)
                end = raw_end

            # Include code name when present after prepositions (du/de/d')
            # Extract longer context to capture "1745 du COD" not just "1745"
            lookahead_end = min(len(text), raw_end + 80)
            lookahead = text[raw_start:lookahead_end]
            
            # Match: article_number [du/de/d'] [code_name or code_abbr]
            code_abbr_match = re.match(
                r'^(.+?)\s+(?:du|de|d[\'\s])\s*([A-Z]+\.?|[A-Za-z\s]+)',
                lookahead,
                re.IGNORECASE
            )
            if code_abbr_match:
                # Include code name/abbr in extraction
                full_article = code_abbr_match.group(0).strip()
                end = raw_start + len(full_article)
                matched_raw = full_article
            else:
                # No code name, check if we hit a code boundary and need to truncate
                code_match = _CODE_BOUNDARY_RE.search(lookahead)
                if code_match and code_match.start() < (raw_end - raw_start):
                    end = raw_start + code_match.start()
                    matched_raw = text[raw_start:end]
                    lowered = matched_raw.lower()
                    for suffix in ["du ", "de ", "d'"]:
                        if lowered.endswith(suffix):
                            matched_raw = matched_raw[:-len(suffix)]
                            break
                    end = raw_start + len(matched_raw)
                    # After truncation, validate that remaining text is still a valid article
                    matched_normalized = normalize_article_number(matched_raw)
                    if not matched_normalized:
                        # Truncation destroyed the article structure; skip this boundary case
                        continue
                else:
                    matched_raw = text[raw_start:raw_end]
                    end = raw_end

            matched = matched_raw.strip()
            if matched:
                mentions.append((matched, raw_start, end))

            cursor = raw_end
            enum_match = ENUM_SEP_RE.match(text[cursor:])
            if not enum_match:
                break

            next_cursor = cursor + enum_match.end()
            if not ARTICLE_TOKEN_RE.match(text[next_cursor:]):
                break
            cursor = next_cursor

        for matched, start, end in mentions:
            ctx_start = max(0, start - window_size // 2)
            ctx_end = min(len(text), end + window_size // 2)
            context_window = text[ctx_start:ctx_end]
            yield (matched, start, end, context_window)
