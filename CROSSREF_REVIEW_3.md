# CROSSREF_REVIEW_3.md — Deep Implementation Audit

**Date:** 2026-06-30  
**Scope:** Full audit of `crossreference/` module + `database/cross_reference_manage.py` + `database/graph_manage.py` against `CROSSREFERENCE.md` specification.  
**Prior reviews:** `CROSSREF_REVIEW.md` (normalizer tail, catalog regressions — fixed), `CROSSREF_REVIEW_2.md` (L.O. normalizer, VU pattern, docstring, naming — fixed).  
**Test suite:** All 52 tests pass (extractor, normalizer, alias_detector, catalog_helpers, resolver_ambiguity).

---

## 1. Executive Summary

The crossreference pipeline is **production-ready for its intended scope** (Phases 0–3 of §16). All critical issues from prior reviews (C1: L.O. normalizer, C2: VU pattern) have been resolved. The `PIPELINE_VERSION` was bumped to `2026.06.30-1` to force reprocessing.

The pipeline now correctly implements:
- Full resolution cascade (A→B→C→D→E→F)
- Correct article extraction with enumeration support
- L.O. prefix gap closure and trailing code-clause stripping
- VU-section detection including `Considérant`, `Aux termes`, `Sur le moyen/fondement`
- Closed-enum `code_family`, stable `code_label`
- Triple-hash incremental skip (`source_hash` + `catalog_hash` + `pipeline_version`)
- Graph-retry with `graph_sync_ok` flag and stale-edge cleanup
- Mention hash excludes target (correct identity semantics per §12.1)

**Remaining issues are tier 2–3 (enhancement, recall, hardening).**

| Tier | Count | Description |
|------|-------|-------------|
| Medium (recall gap) | 2 | Specific article patterns unextracted, repeated-chunk boost blind spot |
| Medium (operational) | 2 | Graph-retry logic quirk, missing COALESCE defense |
| Low (enhancement) | 4 | Test coverage, evaluation framework, OCR fuzzy edge, spec doc gaps |

---

## 2. Medium Issues

### M1. `ARTICLE_TOKEN_RE` cannot match `<digits> <ALPHA>-<digits> <ordinal>` patterns

**File:** `crossreference/_patterns.py:17-32`

**Problem:** The spec §7.5 lists `10 G-0 bis` as an observed live `legi.number` pattern. The current `ARTICLE_TOKEN_RE` cannot match it — it only captures `10 G` (partial match). The leftover `-0 bis` is dropped.

**Root cause:** The regex structure handles:
- `\d+(?:-\d+)* (?:[A-Z]{1,3})* (?:ordinal)* (?:[A-Z]{1,3})*` — i.e., hyphens ONLY between the initial numeric groups

But the pattern `10 G-0 bis` has the structure:
- `<digits> <alpha> <hyphen-digits> <ordinal>` — hyphen appears AFTER an alpha suffix

**Observed behavior:**
```
'10 G-0 bis'    → partial match '10 G'   (INCORRECT)
'150-0 A'       → full match              (correct)
'150-0 A bis'   → full match              (correct)
```

**Impact:** Any JADE/BOFIP mention citing articles with this numbering structure (e.g., `article 10 G-0 bis du CGI`) will:
1. Be extracted as `10 G` instead of `10 G-0 bis`
2. Normalize to `"10 G"` instead of `"10 G-0 BIS"`
3. Either resolve to the wrong catalog row (if `10 G` exists as a separate article) or fail to resolve entirely

**Frequency:** This pattern exists in live LEGI data but is relatively rare (mostly in CGI). A corpus check would quantify the hit count.

**Fix:** Extend the `ARTICLE_TOKEN_RE` numeric-branch to optionally consume `<alpha>-<digits>` after the initial `<digits>` group:

```python
# Current second branch:
\d+(?:-\d+)*
(?:\s+(?-i:[A-Z]{1,3}))*
(?:\s+(?:bis|ter|...))?
(?:\s+(?-i:[A-Z]{1,3}))*

# Proposed (add optional alpha-hyphen-digits continuation):
\d+(?:-\d+)*
(?:\s+(?-i:[A-Z]{1,3})(?:-\d+(?:-\d+)*)?)*   # <-- alpha can be followed by -digits
(?:\s+(?:bis|ter|...))?
(?:\s+(?-i:[A-Z]{1,3})(?:-\d+(?:-\d+)*)?)*
```

**Risk:** This change broadens the regex. Add test cases for known ambiguity traps (e.g., ensure `"10 G du CGI"` doesn't absorb `"du"` as digits). Also need to update normalizer tests.

**Spec reference:** §7.5 explicitly lists `10 G-0 bis` as an observed format.

---

### M2. Graph-retry path rebuilds mentions unnecessarily

**File:** `crossreference/pipeline.py:115-148`

**Problem:** When `hashes_match` is true but `graph_sync_ok` is false (meaning content hasn't changed, just graph injection previously failed), the pipeline:
1. Re-extracts and re-resolves all mentions from scratch
2. Deletes old mentions/edges from PostgreSQL
3. Reinserts them
4. Retries graph injection

Per spec §14.3: "if `graph_sync_ok` is false → retry graph injection only."

The current implementation does a full rebuild (extraction + resolution + PostgreSQL write) when the spec intends only a graph-retry. Since content and catalog haven't changed, the rebuilt mentions should be byte-identical — so the logic is **functionally correct** but:
- Wastes CPU on re-extraction/re-resolution for large documents
- Incurs unnecessary PostgreSQL DELETE + INSERT churn
- On a 60k-document JADE corpus, this could add significant runtime if FalkorDB was temporarily down during a prior run

**Spec intent (§14.3):** "else if `graph_sync_ok` is false → retry graph injection only"

**Fix:** Replace the graph-retry branch with:
```python
if hashes_match and not stored_state.get("graph_sync_ok"):
    graph_sync_ok = inject_cross_reference_edges(src, doc_info["doc_id"])
    _upsert_source_state(
        source_type=src,
        source_doc_id=doc_info["doc_id"],
        source_hash=current_hash,
        catalog_hash=catalog_hash,
        graph_sync_ok=graph_sync_ok,
    )
    if not graph_sync_ok:
        failed_docs += 1
    continue
```

This trusts that PostgreSQL already has correct mentions/edges (since hashes match) and only retries the graph injection.

**Counter-argument:** The current approach is defensive — if PostgreSQL state got corrupted somehow, the rebuild catches it. This is a tradeoff between efficiency and defensiveness.

---

### M3. Pipeline aggregation doesn't defend against NULL `chunk_xxh64`

**File:** `crossreference/pipeline.py:239-263` (JADE), `crossreference/pipeline.py:267-297` (BOFIP)

**Problem:** The `MD5(string_agg(chunk_xxh64, '' ORDER BY chunk_index))` hash computation produces undefined results if any `chunk_xxh64` is NULL. PostgreSQL's `string_agg` excludes NULLs, so two documents with different NULL patterns could produce the same hash (collision) or the same document could produce different hashes across runs if NULL chunks are added/removed.

**Impact:** Low in practice — the ingestion pipeline always computes `chunk_xxh64`. But if a row is manually inserted or a bug introduces a NULL, it causes phantom reprocessing (hash instability) without any diagnostic.

**Fix:** Add `COALESCE(chunk_xxh64, '')` in the aggregation:
```sql
MD5(string_agg(COALESCE(chunk_xxh64, ''), '' ORDER BY chunk_index)) AS source_hash
```

Or add a filter: `WHERE chunk_xxh64 IS NOT NULL` in the aggregation subquery (but this would silently drop incomplete documents).

---

### M4. `repeated_in_chunks` detection: the set tracks `(normalized, chunk_id)` tuples but checks set SIZE

**File:** `crossreference/pipeline.py:394-401`

**Problem (minor):** The code adds `(normalized, chunk_id)` tuples to a set keyed by `normalized_number`, then checks `len(set) > 1`. Since the set contains tuples, this correctly requires 2+ DISTINCT `(normalized, chunk_id)` pairs. But because `normalized` is always the same value (it's the key), the effective check is just "appears in 2+ distinct chunks" — the normalized component in the tuple is redundant.

This is **functionally correct** per spec §11 ("repeated_in_multiple_chunks") but the tuple wastes memory and adds confusion for future maintainers.

**Fix (trivial):** Change to track only chunk_ids:
```python
repeated_in_chunks_map.setdefault(normalized, set()).add(raw["source_chunk_id"])
```

---

## 3. Low-Priority Issues

### L1. No evaluation/QA infrastructure (§15)

The spec defines a silver-set validation framework using existing `legi.links` CITATION entries (§15.1), a 200+200 gold set (§15.2), and failure-bucket monitoring (§15.3). None of these exist.

**Impact:** Cannot measure precision/recall quantitatively. Cannot detect regressions from normalizer/extractor changes.

**Recommendation:**
1. Create `crossreference/eval/` with:
   - `silver_set.py`: extract CITATION links from `legi.links` and compare against resolver output
   - `metrics.py`: compute precision/recall/F1 by source_type, family, and method
2. Add `main.py eval_crossreferences` command
3. Add failure-bucket counters as structured JSON output

**Effort:** Medium-Large (2-3 days)

---

### L2. Missing test files for confidence, fuzzy_resolver, semantic_resolver, pipeline

| Module | Test Coverage |
|--------|--------------|
| `confidence.py` | **NONE** |
| `fuzzy_resolver.py` | **NONE** |
| `semantic_resolver.py` | **NONE** (hard without DB) |
| `pipeline.py` | **NONE** (integration territory) |

**Recommended additions:**
1. `test_confidence.py` — all base scores, all adjustments, clamping, threshold boundary
2. `test_fuzzy_resolver.py` — `_structure_changed` function (unit-testable without DB)
3. Integration test with mocked catalog for the full cascade

The confidence scorer is pure logic with no DB dependency — tests are trivial to add and high-value.

---

### L3. Spec §10.4 `_structure_changed` rejects EITHER change independently — matches current spec wording

**File:** `crossreference/fuzzy_resolver.py:204-221`

The spec §10.4 now says "Either change alone is sufficient to reject" (post-clarification). The implementation rejects when EITHER numeric sequence changes OR alpha prefix changes — this is **correct**.

The Review 2 suggestion to relax to "BOTH must change" was based on an earlier draft. The current code is aligned with the spec. **No action needed.**

However, the `_structure_changed` check does reject legitimate OCR cases where a single digit is corrupted (e.g., `1O12` → `1012` if OCR turned `0` into `O`). This is acceptable given the spec's precision-first stance for fuzzy matching.

---

### L4. Spec documentation gaps (minor enhancements to CROSSREFERENCE.md)

1. **§14.2 — `cross_reference_source_state` schema:** The spec mentions `source_hash`, `catalog_hash`, and `pipeline_version` in §17 rule 13 but never fully describes the `cross_reference_source_state` table in a dedicated section. The implementation adds it at create-tables time. The spec should formalize this table (it's already in the implementation DDL at `cross_reference_manage.py:232-243`).

2. **§7.5 — `10 G-0 bis` pattern:** Listed as an observed format but ARTICLE_TOKEN_RE doesn't support it. Either the spec should note this as a known gap (Phase 4), or the regex should be extended (see M1).

3. **§14.3 — "retry graph injection only":** The spec says graph-retry should not rebuild. The implementation currently rebuilds. One or the other should be adjusted for consistency.

---

## 4. Spec Compliance Matrix (Updated)

| Spec Section | Status | Notes |
|---|---|---|
| §1.1-1.4 (data model) | ✅ | Correctly aggregates by doc_id, temporal filter complete |
| §1.5 (existing links) | ✅ Acknowledged | Not leveraged (§15 not implemented) |
| §1.6 (FalkorDB model) | ✅ | Correct node labels, doc_id property |
| §1.7 (init_graph_schema) | ✅ | Called on every run (idempotent) |
| §1.8 (LEGI ingestion) | ✅ | `_extract_leading_code_label` correctly strips chains |
| §2.1-2.4 (scope) | ✅ | Core families + OTHER_CODE, decrees excluded |
| §3 (architecture) | ✅ | Separate pipeline, incremental, rerunnable |
| §4.1 (catalog schema) | ✅ | All columns, LEGITEXT% filter |
| §4.2 (mentions schema) | ✅ | All columns |
| §4.3 (edges schema) | ✅ | Correct PK |
| §5.1-5.4 (catalog build) | ✅ | code_label, code_family enum, aliases correct |
| §6.1 (JADE aggregation) | ✅ | Uses chunk_text for extraction |
| §6.2 (BOFIP aggregation) | ✅ | Uses text for extraction |
| §7.1-7.2 (chunk-level + relation kind) | ✅ | Correct applies_to/interprets |
| §7.3 (VU detection) | ✅ Fixed | Full pattern set now present |
| §7.4 (BOFIP segmentation) | ✅ | text per chunk with context |
| §7.5-7.6 (parser + token grammar) | ⚠️ Partial | `10 G-0 bis` pattern not supported (M1) |
| §7.7 (extractor output contract) | ✅ | Five-tuple yield correct |
| §8.1 (primary normalized) | ✅ Fixed | L.O. gap closed |
| §8.2 (loose normalized) | ✅ | Space-removal only |
| §8.3 (no hyphenless key) | ✅ | Hyphens preserved |
| §9.1-9.3 (alias detection) | ✅ | Core + extended, correct output shape |
| §10.1 (Step A) | ✅ | Exact + temporal + scope |
| §10.2 (Step B) | ✅ | Loose key same scope |
| §10.3 (Step C) | ✅ | Family-prior + generic rejection |
| §10.4 (Step D) | ✅ | Structure check, min_score 96, delta ≥ 2 |
| §10.5 (Step E) | ✅ | Correct <=> distance, model formatting, scope |
| §10.6 (Step F) | ✅ | Ambiguity resolver with family preference tiers |
| §11 (confidence) | ✅ | All base scores + adjustments match spec |
| §12.1 (mention hash) | ✅ | Source-span based, no target in hash |
| §12.2-12.3 (edge aggregation) | ✅ | Correct grouping, per-doc rebuild |
| §13.1-13.4 (graph injection) | ✅ | APPLIES_TO/INTERPRETS, node check, no placeholders |
| §14.1-14.3 (incremental) | ⚠️ Minor | Graph-retry path rebuilds instead of retry-only (M2) |
| §15 (evaluation/QA) | ❌ Not implemented | See L1 |
| §16 (phases) | ✅ Phases 0-3 | 4-5 future work |
| §17 rules 1-13 | ✅ | All rules satisfied |

---

## 5. Specific Fix Instructions for Next Agent

### Priority 1 (Medium — Recall Improvement)

**M1 Fix — `ARTICLE_TOKEN_RE` pattern extension for `<digits> <ALPHA>-<digits>` articles:**

In `crossreference/_patterns.py`, modify the second branch of `ARTICLE_TOKEN_RE` to allow alpha suffixes to be followed by hyphen-digit chains:

```python
# Current pattern (line 25-28):
\d+(?:-\d+)*
(?:\s+(?-i:[A-Z]{1,3}))*
(?:\s+(?:bis|ter|quater|...))?
(?:\s+(?-i:[A-Z]{1,3}))*

# Proposed pattern:
\d+(?:-\d+)*
(?:\s+(?-i:[A-Z]{1,3})(?:-\d+(?:-\d+)*)?)*
(?:\s+(?:bis|ter|quater|...))?
(?:\s+(?-i:[A-Z]{1,3})(?:-\d+(?:-\d+)*)?)*
```

Similarly update the first branch (letter-prefixed) for consistency.

**Testing requirements:**
1. Add to `test_normalizer.py`:
   ```python
   ("article 10 G-0 bis", "10 G-0 BIS"),
   ("article 10 B-0 du CGI", "10 B-0"),
   ("article 1 A-0 bis du CGI", "1 A-0 BIS"),
   ```
2. Add to `test_extractor.py`:
   ```python
   def test_alpha_hyphen_digit_pattern():
       mentions = _mentions("article 10 G-0 bis du CGI")
       assert len(mentions) == 1
       _, token, _, _, _ = mentions[0]
       assert token == "10 G-0 bis"
       assert normalize_article_number(token) == "10 G-0 BIS"
   ```
3. Verify existing tests still pass (especially enumeration tests where greedy alpha consumption could regress).

**After fix: bump `PIPELINE_VERSION`** to `"2026.06.30-2"` to force reprocessing.

---

### Priority 2 (Medium — Operational)

**M2 Fix — Graph-retry should not rebuild mentions:**

In `crossreference/pipeline.py`, replace lines 115-148 with:

```python
if hashes_match and not stored_state.get("graph_sync_ok"):
    # Content unchanged, catalog unchanged, pipeline version unchanged.
    # PostgreSQL already has correct mentions/edges. Only retry graph injection.
    graph_sync_ok = inject_cross_reference_edges(src, doc_info["doc_id"])
    _upsert_source_state(
        source_type=src,
        source_doc_id=doc_info["doc_id"],
        source_hash=current_hash,
        catalog_hash=catalog_hash,
        graph_sync_ok=graph_sync_ok,
    )
    if not graph_sync_ok:
        failed_docs += 1
        logger.error(
            f"[{src}] Graph sync retry failed for doc {doc_info['doc_id']}"
        )
    else:
        logger.debug(
            f"[{src}] {doc_info['doc_id']}: graph sync retry succeeded"
        )
    continue
```

**M3 Fix — NULL chunk_xxh64 defense:**

In `crossreference/pipeline.py`, modify both JADE (line 247) and BOFIP (line 280) aggregation queries:

```sql
MD5(string_agg(COALESCE(chunk_xxh64, ''), '' ORDER BY chunk_index)) AS source_hash
```

---

### Priority 3 (Low — Test Coverage & Evaluation)

**L2 Fix — Add `test_confidence.py`:**

```python
"""Unit tests for crossreference.confidence."""

import pytest
from crossreference.confidence import score_confidence


@pytest.mark.parametrize("method,expected_base", [
    ("exact_number_and_explicit_code", 0.99),
    ("exact_number_and_temporal_unique", 0.96),
    ("exact_loose_and_explicit_code", 0.92),
    ("exact_loose", 0.85),
    ("exact_number_core_family_only", 0.84),
    ("fuzzy_scoped", 0.78),
    ("semantic_scoped", 0.65),
    ("unresolved", 0.0),
])
def test_base_scores(method, expected_base):
    conf, _ = score_confidence(method, "jade")
    # No alias -> -0.05 penalty
    assert abs(conf - (expected_base - 0.05)) < 0.001


def test_vu_boost_jade_only():
    conf_jade, _ = score_confidence(
        "exact_number_and_explicit_code", "jade",
        detected_code_alias="cgi", mention_in_vu_section=True,
    )
    conf_bofip, _ = score_confidence(
        "exact_number_and_explicit_code", "bofip",
        detected_code_alias="cgi", mention_in_vu_section=True,
    )
    assert conf_jade == 1.0  # 0.99 + 0.05 clamped
    assert conf_bofip == 0.99  # bofip doesn't get VU boost


def test_generic_penalty_stacks():
    conf, _ = score_confidence(
        "semantic_scoped", "jade",
        detected_code_alias=None, is_generic=True,
    )
    # 0.65 - 0.05 (no alias) - 0.10 (generic) = 0.50
    assert abs(conf - 0.50) < 0.001
    assert conf < 0.55  # Below acceptance threshold


def test_clamp_to_0_1():
    conf, _ = score_confidence(
        "unresolved", "jade", is_generic=True,
    )
    assert conf == 0.0  # Cannot go below 0
```

---

## 6. Architecture Observations (Confirmed Good)

These patterns were verified and are well-implemented:

1. **Two-field mention (`matched_text` vs `article_token`)** — Clean separation: `matched_text` carries code-name tail for provenance/alias detection; `article_token` is the normalizer input. The pipeline correctly passes `article_token` (not `matched_text`) to `normalize_article_number`.

2. **Shared `_patterns.py`** — Single source of truth for `ARTICLE_TOKEN_RE` and `PREPOSITION_RE`. Both extractor and normalizer import from here.

3. **`(?-i:[A-Z]{1,3})` inline flag** — Prevents `re.IGNORECASE` from absorbing lowercase French words. Tested and confirmed working.

4. **`PIPELINE_VERSION` invalidation** — Correctly forces full reprocess. The value `"2026.06.30-1"` reflects the most recent fixes.

5. **Graph sync retry** — The `graph_sync_ok` column separates "inference succeeded" from "graph injection succeeded," allowing retry without full recomputation.

6. **Extended alias max-length guard** — `_EXTENDED_ALIAS_MAX_LEN = 80` prevents catalog regressions.

7. **Mention hash identity** — `_compute_mention_hash` excludes `target_legi_doc_id`. Verified to produce identical output to spec's reference implementation.

8. **Defensive enum guard** — `_CATALOG_FAMILY_ENUM` in `cross_reference_manage.py` ensures only `{CGI, LPF, CIBS, OTHER_CODE}` values enter the catalog.

9. **Stale-edge cleanup** — Graph injection removes edges not in the current `desired_target_ids` set, preventing orphaned edges from prior resolutions.

10. **`_IMMEDIATE_PREPOSITION_RE` anchoring** — Requires the preposition immediately after the article token, preventing `"articles 38, 39 et 39 A du CGI"` from attaching the tail to every enumeration item.

---

## 7. Summary of Actions

| # | Issue | Priority | Effort | Files |
|---|---|---|---|---|
| M1 | ARTICLE_TOKEN_RE can't match `10 G-0 bis` | Medium | Medium | `_patterns.py`, tests |
| M2 | Graph-retry rebuilds unnecessarily | Medium | Small | `pipeline.py` |
| M3 | NULL chunk_xxh64 defense | Medium | Trivial | `pipeline.py` |
| M4 | Redundant tuple in repeated_in_chunks | Medium | Trivial | `pipeline.py` |
| L1 | Evaluation framework (§15) | Low | Large | New `crossreference/eval/` |
| L2 | Missing test files | Low | Medium | `tests/crossreference/` |
| L4 | CROSSREFERENCE.md doc gaps | Low | Small | `CROSSREFERENCE.md` |
| — | Bump PIPELINE_VERSION after M1 | Required | Trivial | `_version.py` |

---

## 8. Risk Assessment

**What could silently degrade precision (false positives):**
- None identified. The confidence scoring correctly penalizes low-signal mentions. The acceptance threshold (0.55) is calibrated against the base scores and penalties.

**What could silently degrade recall (missed links):**
- M1: Articles with `<digits> <ALPHA>-<digits>` patterns are partially extracted (only the `<digits> <ALPHA>` portion), potentially matching wrong catalog rows.
- Articles that appear only in JADE `ANA`-stored decisions without VU structure (acknowledged in spec §7.3 as a known limitation).

**What could cause runtime issues:**
- M2: If FalkorDB is down for an extended period, the next run will redundantly rebuild PostgreSQL data for all previously-incomplete documents. On a 60k-document corpus, this adds ~minutes of wasted compute per run.
- M3: Extremely unlikely given the ingestion pipeline, but a NULL `chunk_xxh64` would make source_hash unstable.

---

## 9. Conclusion

The crossreference pipeline is a solid, spec-aligned implementation covering Phases 0–3 of the development roadmap. The remaining work is:
1. **Recall improvement** (M1: regex extension for rare article patterns)
2. **Operational hardening** (M2/M3: efficiency fixes)
3. **Quality measurement** (L1: evaluation framework)
4. **Test coverage** (L2: confidence/fuzzy/integration tests)

None of these block production use. The pipeline correctly resolves deterministic, fuzzy, and semantic matches with proper scoping, temporal filtering, and confidence scoring. The graph injection is resilient to transient FalkorDB failures. The incremental processing correctly invalidates on source, catalog, or pipeline changes.
