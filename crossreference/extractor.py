"""Article token extraction from source text.

Two-step extraction:
1. Find anchor (article/articles/art.)
2. Parse following article token(s) until punctuation or code-name boundary

Each yielded mention carries two spans:
- ``article_token``: the raw article token (number + letter prefix + suffix),
  used as the lookup key for the normalizer.
- ``matched_text``: the article token plus an optional trailing code-name
  clause captured via a preposition (du/de la/de l'/au/aux), kept for human
  provenance and confidence scoring.
"""

import re
import unicodedata

from crossreference._patterns import ARTICLE_TOKEN_RE


ANCHOR_RE = re.compile(
    r"(?:article|articles|art\.)\s+",
    re.IGNORECASE,
)

ENUM_SEP_RE = re.compile(r"\s*(?:,|et|ou)\s+")

# French prepositions/articles that ARTICLE_TOKEN_RE's alpha-tail greedily
# absorbs (because they look like 1-3 uppercase letters after case-insensitive
# match). Post-trim them off so article_token stays a clean article number.
_ARTICLE_TOKEN_ALPHA_TAIL_RE = re.compile(
    r"\s+(?:du|des|de|la|le|les|l|au|aux|d|et|ou)$",
    re.IGNORECASE,
)

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

# Extended preposition matched immediately after the article token. We do NOT
# allow `.+?` between the token and the preposition: that earlier shape made
# the first article in an enumeration like "articles 38, 39 et 39 A du CGI"
# greedily swallow the rest of the enumeration as `article_part`. Anchoring
# the preposition right at the token boundary means the code-name tail is
# attached only to the article that genuinely owns it (the last item in the
# enumeration here).
_IMMEDIATE_PREPOSITION_RE = re.compile(
    r"^\s+(?:du|des|de\s+la|de\s+l['’]|de\s+l\s+|au|aux|de)\s+",
    re.IGNORECASE,
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
        (matched_text, article_token, start_pos, end_pos, context_window)

    - ``matched_text`` carries the rich span including any code-name tail.
    - ``article_token`` carries only the article number span and is the
      canonical input for :func:`normalize_article_number`.
    - ``context_window`` is ~200 chars around the match for semantic/alias
      context.
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

            # Strip French preposition/article tails that ARTICLE_TOKEN_RE
            # greedily captured as fake alpha suffixes (e.g. "1745 du").
            token_clean = matched_raw
            while True:
                tail_match = _ARTICLE_TOKEN_ALPHA_TAIL_RE.search(token_clean)
                if not tail_match:
                    break
                token_clean = token_clean[: tail_match.start()].rstrip()
            article_token = token_clean

            # Extend matched_text with "du/de/de la/de l'/au/aux <code-name>" if
            # the preposition appears IMMEDIATELY after the article token. This
            # keeps the code clause on the article that actually owns it: in
            # "articles 38, 39 et 39 A du CGI" only "39 A" gets the tail.
            # article_token is preserved unchanged; only matched_text grows.
            article_part = matched_raw
            lookahead_end = min(len(text), raw_end + 200)
            tail_text = text[raw_end:lookahead_end]

            prepos_match = _IMMEDIATE_PREPOSITION_RE.match(tail_text)
            if prepos_match:
                code_start = prepos_match.end()
                code_text = tail_text[code_start:]

                code_boundary_match = _CODE_BOUNDARY_RE.search(remove_accents(code_text))
                if code_boundary_match:
                    code_part = code_text[:code_boundary_match.end()].strip()
                else:
                    stop_match = re.search(r"[,;\n]|(?:article|art\.)\s", code_text)
                    if stop_match:
                        code_part = code_text[:stop_match.start()].strip()
                    else:
                        words = code_text.split()[:5]
                        code_part = " ".join(words)

                if code_part:
                    connector_stop = re.search(
                        r"\s+(?:qui|que|qu'|c'est|c'était|que\s+la|que\s+le|qu'il|qu'elle)\b",
                        code_part,
                        re.IGNORECASE,
                    )
                    if connector_stop:
                        code_part = code_part[:connector_stop.start()].strip()
                    else:
                        verb_stop = re.search(
                            r"\s+(impliquent|contester|établit|imposent|déduit|reste|demeurent)\b",
                            code_part,
                            re.IGNORECASE,
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
                matched_raw = text[raw_start:raw_end]
                end = raw_end

            matched = matched_raw.strip()
            token = article_token.strip()
            if matched and token:
                mentions.append((matched, token, raw_start, end))

            cursor = raw_end
            enum_match = ENUM_SEP_RE.match(text[cursor:])
            if not enum_match:
                break

            next_cursor = cursor + enum_match.end()
            if not ARTICLE_TOKEN_RE.match(text[next_cursor:]):
                break
            cursor = next_cursor

        for matched, token, start, end in mentions:
            ctx_start = max(0, start - window_size // 2)
            ctx_end = min(len(text), end + window_size // 2)
            context_window = text[ctx_start:ctx_end]
            yield (matched, token, start, end, context_window)
