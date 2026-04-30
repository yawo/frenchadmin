"""Article token extraction from source text.

Two-step extraction:
1. Find anchor (article/articles/art.)
2. Parse following article token(s) until punctuation or code-name boundary
"""

import re
import unicodedata


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


def remove_accents(text: str) -> str:
    """Remove accents from text for matching."""
    return ''.join(
        c for c in unicodedata.normalize('NFD', text)
        if unicodedata.category(c) != 'Mn'
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

            # Include full code name after prepositions (du/de/d')
            # Stop at: known code boundary, or max 5 words, or punctuation
            lookahead_end = min(len(text), raw_end + 200)
            lookahead = text[raw_start:lookahead_end]
            
            # Match: article [du/de/d'] then capture code name
            prepos_match = re.match(
                r"^(.+?)\s+(?:du|de|d['\s])\s+",
                lookahead,
                re.IGNORECASE,
            )
            if prepos_match:
                article_part = prepos_match.group(1).strip()
                code_start = prepos_match.end()
                code_text = lookahead[code_start:]
                
                # Find known code boundary pattern or stop early
                code_boundary_match = _CODE_BOUNDARY_RE.search(remove_accents(code_text))
                if code_boundary_match:
                    # Include matched code boundary
                    code_part = code_text[:code_boundary_match.end()].strip()
                else:
                    # No known boundary, stop at: punctuation, or max 5 words
                    stop_match = re.search(r"[,;\n]|(?:article|art\.)\s", code_text)
                    if stop_match:
                        code_part = code_text[:stop_match.start()].strip()
                    else:
                        # Take max 5 words
                        words = code_text.split()[:5]
                        code_part = " ".join(words)
                
                # Validate: code_part should not contain sentence connectors
                # Remove trailing verbs like "impliquent", "contester", etc.
                if code_part:
                    # Stop at relative pronouns/conjunctions or main verbs
                    connector_stop = re.search(
                        r"\s+(?:qui|que|qu'|c'est|c'était|que\s+la|que\s+le|qu'il|qu'elle)\b",
                        code_part,
                        re.IGNORECASE
                    )
                    if connector_stop:
                        code_part = code_part[:connector_stop.start()].strip()
                    else:
                        # Stop at known verb patterns that indicate end of code reference
                        verb_stop = re.search(
                            r"\s+(impliquent|contester|établit|imposent|déduit|reste|demeurent)\b",
                            code_part,
                            re.IGNORECASE
                        )
                        if verb_stop:
                            code_part = code_part[:verb_stop.start()].strip()
                
                if code_part:
                    full_article = f"{article_part} du {code_part}"
                    matched_raw = full_article
                    end = raw_start + len(matched_raw)
                else:
                    matched_raw = text[raw_start:raw_end]
                    end = raw_end
            else:
                # No preposition, use article token only
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
