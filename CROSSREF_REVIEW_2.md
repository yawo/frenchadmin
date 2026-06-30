# CROSSREF_REVIEW_2.md — Implementation Audit Against CROSSREFERENCE.md

**Date:** 2026-06-30
**Scope:** Full audit of `crossreference/` module + `database/cross_reference_manage.py` + `database/graph_manage.py` (graph injection) against the specification in `CROSSREFERENCE.md`.

**Prior review:** `CROSSREF_REVIEW.md` identified the normalizer/extractor asymmetry (normalized_number carrying the code-name tail) and the catalog `code_label`/`code_family` violations. Those issues have since been **fixed** — the current codebase is aligned on these points. This review audits the post-fix state.

---

## 1. Executive Summary

The crossreference pipeline is substantially complete and well-structured. The core cascade (extract → normalize → resolve deterministically → fuzzy → semantic → aggregate → graph inject) is functional and aligned with the spec. The CROSSREF_REVIEW.md issues (normalizer tail stripping, catalog code_label/code_family enum compliance) have been fixed.

**Remaining issues fall into three tiers:**

| Tier | Count | Impact |
|------|-------|--------|
| Critical (correctness) | 2 | Silent precision/recall loss on specific article patterns |
| Medium (spec gap) | 5 | Incomplete coverage, misleading naming, recall loss |
| Low (hardening) | 4 | Operational maturity, evaluation, edge cases |

---

## 2. Critical Issues

### C1. `L.O.` prefix not handled by normalizer `_LETTER_PREFIX_GAP_RE`

**File:** `crossreference/normalizer.py:33`

The ARTICLE_TOKEN_RE in `_patterns.py` correctly recognizes `L.O.` as a valid prefix (line 20: `(?:L\.O\.|[LRDA])`). However, the normalizer's `_LETTER_PREFIX_GAP_RE` only collapses `[LRDA]` + optional dot + space:

```python
_LETTER_PREFIX_GAP_RE = re.compile(r"(?i)\b([LRDA])(?!\s*\*)\.?\s+(\d)")
```

This means:
- `L.O. 234-5` → extracted correctly by ARTICLE_TOKEN_RE
- But `_LETTER_PREFIX_GAP_RE` won't collapse "L.O. 234" to "L.O.234"
- The normalized form keeps the internal space: `"L.O. 234-5"` (uppercased)
- The catalog's `normalized_number` for a LEGI row with `number="L.O.234-5"` would be `"L.O.234-5"` (no space, since there's no space in the stored LEGI number)
- **Result:** Step A exact match fails for any `L.O.` article because normalized_number from the mention (`L.O. 234-5`) ≠ catalog normalized_number (`L.O.234-5`)

**Fix:** Add a dedicated L.O. collapse rule after the existing `_LETTER_PREFIX_GAP_RE` substitution:

```python
_LO_PREFIX_GAP_RE = re.compile(r"(?i)\bL\.O\.\s+(\d)")
# Apply: text = _LO_PREFIX_GAP_RE.sub(r"L.O.\1", text)
```

**Spec reference:** §7.6 lists `L.O.` as a required prefix; §8.1 says "collapse `<letter> .? <space>+ <digit>` → `<letter><digit>`" which by analogy should apply to `L.O.` as well.

---

### C2. VU-section detection pattern is incomplete

**File:** `crossreference/pipeline.py:43-45`

Current pattern:

```python
_VU_PATTERN = re.compile(
    r'\b(?:Vu\s+la|Vu\s+le|Vu\s+.*?procedure|VU)\b',
    re.IGNORECASE,
)
```

Spec §7.3 lists these high-signal markers for JADE:
- `^VU\b` ✓ (partial — not anchored to start of line)
- `^Vu la procedure` ✓ (covered by `Vu\s+.*?procedure`)
- `^Considérant` ✗ **MISSING**
- `^Aux termes de l'article` ✗ **MISSING**
- `^Sur` ✗ **MISSING**

The missing patterns are specifically called out in the spec as "high-signal markers" for scoring mentions higher. Their absence means JADE mentions near `Considérant que l'article...` or `Aux termes de l'article...` sections receive no VU boost (+0.05 confidence), potentially dropping them below the acceptance threshold.

**Impact:** Recall loss for JADE mentions in reasoning sections (which are the most common location for article citations in French administrative court decisions).

**Fix:** Extend the pattern:

```python
_VU_PATTERN = re.compile(
    r'\b(?:Vu\s+la|Vu\s+le|Vu\s+.*?proc[eé]dure|VU|Consid[eé]rant|Aux\s+termes\s+de\s+l|Sur\s+le)\b',
    re.IGNORECASE,
)
```

---

## 3. Medium Issues

### M1. Normalizer docstring contains incorrect example

**File:** `crossreference/normalizer.py:71`

The docstring says:

```python
normalize_article_number("L. 247 du livre des procedures fiscales") == "L.247"
```

The actual output is `"L247"` (confirmed by testing and the test suite). The spec §8.1 example also says `"L247"`. The code is correct; only the docstring is wrong. This is a maintenance trap for future developers.

**Fix:** Change `"L.247"` to `"L247"` in the docstring.

---

### M2. Semantic resolver threshold variable naming is inverted

**File:** `crossreference/semantic_resolver.py:20-21, 166`

```python
SEMANTIC_MIN_SCORE = 0.75
SEMANTIC_CODE_ALIASED_MIN = 0.85
...
min_score = SEMANTIC_MIN_SCORE if has_code_alias else SEMANTIC_CODE_ALIASED_MIN
```

The **logic is correct** per spec §11: code alias detected → 0.75 threshold; no alias → 0.85. But the variable named `SEMANTIC_CODE_ALIASED_MIN` is used for the **no-alias** case, which is the opposite of what the name suggests.

**Impact:** Future maintainer reads `SEMANTIC_CODE_ALIASED_MIN = 0.85`, interprets it as "threshold when code alias is present", and introduces a bug when modifying thresholds.

**Fix:** Rename to clarify intent:

```python
SEMANTIC_THRESHOLD_WITH_ALIAS = 0.75    # Lower threshold: family context reduces ambiguity
SEMANTIC_THRESHOLD_NO_ALIAS = 0.85      # Higher threshold: no family context, need strong similarity
```

---

### M3. `repeated_in_chunks` detection counts unique chunk appearances, not unique mention positions

**File:** `crossreference/pipeline.py:394-401`

```python
repeated_in_chunks_map = {}
for raw in raw_mentions:
    normalized = raw["normalized_number"]
    if not normalized:
        continue
    key = (normalized, raw["source_chunk_id"])
    repeated_in_chunks_map.setdefault(normalized, set()).add(key)
```

The boost fires when `len(set) > 1`, i.e., when the same normalized_number appears in 2+ distinct chunks. This is **correct per the spec** (§11: "repeated_in_multiple_chunks").

However, there's a subtle edge case: if a JADE document mentions "article 1745" three times within the same chunk but in no other chunk, the mention gets no boost. Meanwhile, if it appears once in chunk 0 and once in chunk 1, it gets the boost. This asymmetry is minor but worth documenting — the current behavior correctly reflects cross-chunk presence (stronger signal than intra-chunk repetition).

**No code change needed.** Add a comment explaining the rationale.

---

### M4. Extended alias cache has no invalidation on process restart

**File:** `crossreference/alias_detector.py:53-56, 142-149`

The cache uses `time.time()` with a 15-minute TTL. This is fine for long-running processes, but:
1. The pipeline calls `invalidate_extended_alias_cache()` after catalog refresh (correct).
2. If the process dies and restarts mid-run, the cache starts empty (correct — it refetches).
3. But if `_get_extended_aliases()` is called before `refresh_legi_reference_catalog()` in a custom code path (e.g., a debugging session), it caches stale data for 15 minutes.

**Impact:** Low in production (pipeline always refreshes catalog first). Could cause confusion during development.

**Fix:** Document the ordering constraint clearly, or make the cache structurally depend on the catalog_hash.

---

### M5. Fuzzy resolver `_structure_changed` rejects ANY numeric difference — too strict for OCR drift

**File:** `crossreference/fuzzy_resolver.py:204-221`

```python
def _structure_changed(original: str, matched: str) -> bool:
    orig_nums = re.findall(r"\d+", original)
    match_nums = re.findall(r"\d+", matched)
    if orig_nums != match_nums:
        return True  # Reject when numeric sequence differs
```

Spec §10.4 says fuzzy should handle "OCR spacing drift" like `R* 196-1` vs `R*196-1` and `1012 terA` vs `1012 ter A`. These are spacing differences, not numeric changes, so they pass correctly.

But consider OCR scanning `1O12` (capital-O instead of zero): the normalized form might contain different digit sequences. The spec says to "reject if fuzzy match changes both numeric and alpha structure too much" — the word "both" suggests rejecting only when BOTH change, not when either changes alone. Current code rejects when EITHER changes independently.

**Impact:** Potentially over-rejects valid OCR fuzzy matches where a single digit is corrupted but alpha structure is preserved.

**Fix (optional):** Consider relaxing to reject only when both change simultaneously:

```python
nums_changed = orig_nums != match_nums
alpha_changed = orig_alpha and match_alpha and orig_alpha[0] != match_alpha[0]
return nums_changed and alpha_changed  # Reject only when BOTH change
```

This is a precision/recall tradeoff — discuss with the team before changing.

---

## 4. Low-Priority Issues

### L1. No evaluation/QA infrastructure (§15 not implemented)

The spec defines:
- §15.1: Silver set validation using existing `legi.links` CITATION entries
- §15.2: Gold set of 200 JADE + 200 BOFIP manually-reviewed docs
- §15.3: Failure bucket monitoring (missing alias, wrong family, wrong temporal version, etc.)

None of these exist in the codebase. There's no automated quality measurement framework.

**Recommendation:** Phase this in after the current pipeline is stable:
1. Add a `crossreference/eval/` subpackage with silver-set extraction and metric computation
2. Add a `main.py eval_crossreferences` command that samples resolved edges and computes precision/recall against the silver set
3. Track failure buckets as structured log output or a summary table

---

### L2. `init_graph_schema()` call happens on every pipeline run

**File:** `crossreference/pipeline.py:67`

```python
init_graph_schema()
```

The spec §1.7 says "the future cross-reference implementation should call `init_graph_schema()` before graph backfill or during startup / `create_tables`". The current implementation calls it on every `infer_crossreferences` run. The function is idempotent (it creates indexes IF NOT EXISTS), so this is safe but slightly wasteful.

**Recommendation:** Move to `create_cross_reference_tables()` or the application startup sequence. Not blocking.

---

### L3. Graph stale-edge cleanup uses `NOT IN` with list parameter

**File:** `database/graph_manage.py:829-839`

```cypher
MATCH (s:{source_label} {doc_id: $source_doc_id})-[r:{edge_label}]->(t:LegalText)
WHERE NOT t.doc_id IN $desired_target_ids
DELETE r
```

This is correct but may not scale well if `desired_target_ids` grows very large. In practice, a single source document rarely produces more than ~50 edges, so this is fine for now.

---

### L4. Pipeline aggregation queries don't filter `WHERE chunk_xxh64 IS NOT NULL`

**File:** `crossreference/pipeline.py:239-263` (JADE), `crossreference/pipeline.py:267-297` (BOFIP)

The `MD5(string_agg(chunk_xxh64, '' ORDER BY chunk_index))` hash computation assumes all chunks have a non-null `chunk_xxh64`. If any are null, `string_agg` would produce a different hash on each run (since NULL concatenation may vary). This is unlikely given the ingestion pipeline always computes hashes, but a defensive `WHERE chunk_xxh64 IS NOT NULL` in the aggregation or a `COALESCE(chunk_xxh64, '')` would prevent phantom reprocessing.

---

## 5. Spec Compliance Matrix

| Spec Section | Status | Notes |
|---|---|---|
| §1.1-1.4 (data model understanding) | ✅ Complete | Correctly uses chunk-level tables, aggregates by doc_id |
| §1.5 (existing links) | ✅ Acknowledged | Not leveraged yet (see §15.1, L1) |
| §1.6 (FalkorDB model) | ✅ Complete | Uses correct node labels and `doc_id` property |
| §1.7 (init_graph_schema) | ✅ Called | Called on every run (see L2) |
| §1.8 (LEGI ingestion peculiarities) | ✅ Fixed | `_extract_leading_code_label` correctly strips subtitle chains |
| §2.1-2.4 (scope) | ✅ Complete | Core families + OTHER_CODE supported, decrees excluded |
| §3 (architecture) | ✅ Complete | Separate `infer_crossreferences` pipeline, incremental, rerunnable |
| §4.1 (catalog schema) | ✅ Complete | All columns present, LEGITEXT% filter applied |
| §4.2 (mentions schema) | ✅ Complete | All columns present |
| §4.3 (edges schema) | ✅ Complete | All columns present, correct PK |
| §5.1-5.4 (catalog build) | ✅ Complete | `code_label` extraction, `code_family` enum, aliases all correct |
| §6.1 (JADE aggregation) | ✅ Complete | Uses `chunk_text` for extraction, avoids duplication |
| §6.2 (BOFIP aggregation) | ✅ Complete | Uses `text` for extraction |
| §7.1-7.2 (chunk-level + relation kind) | ✅ Complete | Correct `applies_to`/`interprets` mapping |
| §7.3 (JADE segmentation / VU detection) | ⚠️ Partial | Missing `Considérant`, `Aux termes`, `Sur` markers (C2) |
| §7.4 (BOFIP segmentation) | ✅ Complete | Uses `text` per chunk with context window |
| §7.5-7.6 (parser + token grammar) | ✅ Complete | Two-step extraction, enumerations, suffixes all handled |
| §7.7 (extractor output contract) | ✅ Complete | Yields (matched_text, article_token, start, end, context_window) |
| §8.1 (primary normalized number) | ⚠️ Mostly complete | `L.O.` gap not handled (C1); all other rules correct |
| §8.2 (loose normalized number) | ✅ Complete | Space-removal only |
| §8.3 (no hyphenless key) | ✅ Complete | Hyphens preserved in both forms |
| §9.1-9.3 (alias detection) | ✅ Complete | Core + extended aliases, correct output shape |
| §10.1 (Step A) | ✅ Complete | Exact + temporal + scope clause cascade |
| §10.2 (Step B) | ✅ Complete | Loose key with same scope |
| §10.3 (Step C) | ✅ Complete | Family-prior with generic rejection |
| §10.4 (Step D) | ⚠️ Minor issue | Over-strict `_structure_changed` (M5) |
| §10.5 (Step E) | ✅ Complete | Correct cosine distance, model formatting, scope |
| §10.6 (Step F) | ✅ Complete | Ambiguity resolver with family preference tiers |
| §11 (confidence scoring) | ✅ Complete | All base scores + adjustments match spec |
| §12.1 (mention hash) | ✅ Complete | Source-span based, no target in hash |
| §12.2-12.3 (edge aggregation) | ✅ Complete | Correct grouping, per-doc rebuild |
| §13.1-13.4 (graph injection) | ✅ Complete | APPLIES_TO/INTERPRETS, node verification, no placeholder targets |
| §14.1-14.3 (incremental) | ✅ Complete | Triple hash (source + catalog + pipeline_version) |
| §15 (evaluation/QA) | ❌ Not implemented | See L1 |
| §16 (implementation phases) | ✅ Phases 0-3 done | Phase 4-5 acknowledged as future work |
| §17 rule 1-8 | ✅ Compliant | |
| §17 rule 9 | ✅ Fixed | `code_family` is now closed enum |
| §17 rule 10 | ✅ Fixed | `code_label` is now leading label only |
| §17 rule 11 | ✅ Compliant | `WHERE category LIKE 'LEGITEXT%'` in catalog refresh |
| §17 rule 12 | ✅ Compliant | Pipeline normalizes from `article_token`, not `matched_text` |
| §17 rule 13 | ✅ Compliant | `source_hash` + `catalog_hash` + `pipeline_version` all checked |

---

## 6. Specific Fix Instructions for Next Agent

### Priority 1 (Critical)

**C1 Fix — L.O. normalizer gap:**

In `crossreference/normalizer.py`, after line 33 add:

```python
_LO_PREFIX_GAP_RE = re.compile(r"(?i)\bL\.O\.\s+(\d)")
```

In `normalize_article_number()`, after line 87 (`text = _LETTER_PREFIX_GAP_RE.sub(...)`) add:

```python
text = _LO_PREFIX_GAP_RE.sub(r"L.O.\1", text)
```

Add test cases to `tests/crossreference/test_normalizer.py`:

```python
("L.O. 234-5", "L.O.234-5"),
("article L.O. 111-9", "L.O.111-9"),
```

**C2 Fix — VU pattern:**

In `crossreference/pipeline.py`, replace lines 43-45:

```python
_VU_PATTERN = re.compile(
    r'\b(?:Vu\s+la|Vu\s+le|Vu\s+.*?proc[eé]dure|VU|Consid[eé]rant|Aux\s+termes\s+de\s+l|Sur\s+le\s+(?:moyen|fondement|bien-fond[eé]))\b',
    re.IGNORECASE,
)
```

Note: `Sur` alone is too broad (captures "Sur les dépens", "Sur ce point", etc.). Restrict to `Sur le moyen`, `Sur le fondement`, `Sur le bien-fondé` which are the patterns that signal article citation context.

### Priority 2 (Medium)

**M1 Fix — Docstring:**

In `crossreference/normalizer.py:71`, change:
```
normalize_article_number("L. 247 du livre des procedures fiscales") == "L.247"
```
to:
```
normalize_article_number("L. 247 du livre des procedures fiscales") == "L247"
```

**M2 Fix — Variable naming:**

In `crossreference/semantic_resolver.py`, rename:
```python
SEMANTIC_MIN_SCORE = 0.75  →  SEMANTIC_THRESHOLD_WITH_ALIAS = 0.75
SEMANTIC_CODE_ALIASED_MIN = 0.85  →  SEMANTIC_THRESHOLD_NO_ALIAS = 0.85
```

Update the reference at line 166 accordingly.

### Priority 3 (Low)

These are enhancement recommendations for the next development cycle:

1. **Evaluation framework (L1):** Create `crossreference/eval/` with silver-set extraction and precision/recall computation. Add a `main.py eval_crossreferences` command.

2. **Bump `PIPELINE_VERSION`** after applying C1/C2 fixes to force reprocessing of all source documents on the next run.

---

## 7. Architecture Observations (No Issues, Confirmed Good)

These patterns are worth calling out as **well-implemented** and spec-aligned:

1. **Two-field mention (matched_text vs article_token)** — Clean separation between provenance/display and lookup key. The extractor's `_IMMEDIATE_PREPOSITION_RE` attaches code-name tails only to the last enumeration item (correct per "articles 38, 39 et 39 A du CGI").

2. **Shared `_patterns.py`** — Single source of truth for ARTICLE_TOKEN_RE and PREPOSITION_RE, used by both extractor and normalizer. Prevents drift.

3. **`(?-i:[A-Z]{1,3})` inline flag** — Prevents `re.IGNORECASE` from absorbing lowercase French words as fake alpha suffixes. Subtle and correct.

4. **Pipeline version invalidation** — `_version.PIPELINE_VERSION` forces full reprocess on inference logic changes, even when source data hasn't changed. This is the only safe approach for pipeline migrations.

5. **Graph sync retry with `graph_sync_ok` flag** — If FalkorDB is down during a run, edges are still computed and stored in PostgreSQL; the graph injection retries on the next run without recomputing mentions.

6. **Extended alias max-length guard** — `_EXTENDED_ALIAS_MAX_LEN = 80` prevents catalog regressions from polluting the alias map with 200-char subtitle chains.

7. **Mention hash excludes `target_legi_doc_id`** — Re-resolution (after normalizer/resolver changes) updates existing mentions in place rather than creating duplicates. This is the correct identity semantic per §12.1.

---

## 8. CROSSREFERENCE.md Doc Enhancement Suggestions

The spec is thorough and well-maintained. Minor additions:

1. **§7.6 — Add explicit L.O. normalization rule:** Currently says "letter prefixes such as `L`, `R`, `D`, `A` (plus `L.O.`)" but §8.1 normalization rules only describe single-letter collapse. Add:
   > `L.O. <space> <digit>` → `L.O.<digit>` (no internal space)

2. **§7.3 — Expand VU markers list to include recommended regex:**
   Add `Considérant`, `Aux termes de l'article`, `Sur le moyen/fondement` to the pattern example so future implementers don't treat the bullet list as exhaustive.

3. **§10.4 — Clarify "changes both numeric and alpha structure too much":**
   The word "both" is ambiguous. Clarify whether rejection requires BOTH numeric AND alpha to change, or EITHER. Current implementation rejects on EITHER. If that's intended, say "changes numeric sequence OR alpha prefix".

4. **§14 — Document `cross_reference_source_state` table:**
   The spec describes the three-hash condition (§17 rule 13) and mentions `source_hash` + `catalog_hash` + `pipeline_version`, but doesn't describe the fourth table (`cross_reference_source_state`) or the `graph_sync_ok` column. This table is the incremental-processing backbone and warrants its own schema section.

---

## 9. Test Coverage Assessment

| Module | Test File | Coverage Assessment |
|---|---|---|
| `extractor.py` | `test_extractor.py` | Good: enumeration, code-tail attachment, star/ordinal, alpha-tail trim |
| `normalizer.py` | `test_normalizer.py` | Good: all spec examples + round-trip. **Missing:** L.O. prefix |
| `alias_detector.py` | `test_alias_detector.py` | Good: core families, OTHER_CODE with parents, cache length guard |
| `resolver.py` | `test_resolver_ambiguity.py` | Partial: only `_resolve_ambiguity`. No integration test for full cascade |
| `confidence.py` | *(none)* | ❌ Missing |
| `fuzzy_resolver.py` | *(none)* | ❌ Missing |
| `semantic_resolver.py` | *(none)* | ❌ Missing (hard to unit-test without DB) |
| `pipeline.py` | *(none)* | ❌ Missing (integration test territory) |
| `cross_reference_manage.py` | `test_catalog_helpers.py` | Partial: `_extract_leading_code_label` only |

**Recommended test additions:**
1. `test_confidence.py` — test all base scores, all adjustments, edge clamping
2. `test_normalizer.py` — add L.O. cases
3. `test_extractor.py` — add L.O. extraction case
4. Integration test with a mocked `legi_reference_catalog` table for the full resolve cascade

---

## 10. Summary of Actions

| # | Issue | Priority | Effort | Files |
|---|---|---|---|---|
| C1 | L.O. normalizer gap | Critical | Small | `normalizer.py`, `test_normalizer.py` |
| C2 | VU pattern incomplete | Critical | Small | `pipeline.py` |
| M1 | Docstring typo | Medium | Trivial | `normalizer.py` |
| M2 | Semantic threshold naming | Medium | Small | `semantic_resolver.py` |
| M5 | Fuzzy structure_changed strictness | Medium | Discuss | `fuzzy_resolver.py` |
| L1 | Evaluation framework | Low | Large | New `crossreference/eval/` |
| L4 | Null chunk_xxh64 defense | Low | Trivial | `pipeline.py` |
| — | Bump PIPELINE_VERSION | Required after C1/C2 | Trivial | `_version.py` |
| — | Add missing test files | Recommended | Medium | `tests/crossreference/` |
| — | Enhance CROSSREFERENCE.md | Recommended | Small | `CROSSREFERENCE.md` |
