# Full-Text Search (FTS) — Findings, Adjustments & Rationales

## Problem Statement

A query like *"rémunération dirigeant associé unique EURL ou SARL"* returned irrelevant results (plus-values d'apport, cautions bancaires, convention franco-néerlandaise) instead of documents about EURL/SARL director compensation (article 62 CGI).

Root causes identified:
1. Pure vector search too loose — semantic similarity matches any doc mentioning SARL tangentially
2. Cross-encoder reranker truncated documents (512 tokens for 7500-char chunks)
3. Reranker scores discarded after ranking — confidence filter used stale cosine similarity
4. No keyword matching signal to anchor results on specific legal terms

---

## Architecture Overview

```
User Query
    │
    ├─── _vector_search_inner()     ← pgvector cosine similarity (HNSW)
    │         returns top_k results
    │
    ├─── _fts_search()              ← PostgreSQL tsvector OR-based query (GIN index)
    │         returns top_k results
    │
    └─── _reciprocal_rank_fusion()  ← RRF merges both ranked lists
              │
              v
         graphrag_search()
              │
              ├── Graph augmentation (FalkorDB)
              ├── Cross-encoder reranking (BAAI/bge-reranker-v2-m3)
              ├── RERANKER_MIN_SCORE filter
              └── min_confidence filter
```

---

## Adjustments Made

### 1. OR-Based FTS Query (not AND)

**Problem**: `plainto_tsquery('french', 'rémunération dirigeant associé unique EURL SARL')` produces:
```
'rémuner' & 'dirig' & 'associ' & 'uniqu' & 'eurl' & 'sarl'
```
This requires ALL terms to match. Since "EURL" and "SARL" don't exist in LEGI text (the law uses the full form "société à responsabilité limitée"), FTS returned **zero results**.

**Fix**: Split user query into individual words and build an OR-combined tsquery:
```sql
plainto_tsquery('french', 'rémunération') || plainto_tsquery('french', 'dirigeant') || ...
```

**Rationale**: OR logic lets documents matching *more* terms rank higher via `ts_rank_cd` without requiring every term. This surfaces articles that use synonyms or full forms (e.g., "gérant" instead of "dirigeant", "société à responsabilité limitée" instead of "SARL").

**File**: `web/services/vector_search.py` — `_fts_search()`

---

### 2. LEGI Temporal Version Deduplication

**Problem**: The CGI contains multiple temporal versions of the same article, each with a unique `doc_id` (LEGIARTI). A query matching article 261 (TVA exemptions) returned 14 versions of the same content, consuming all FTS result slots.

**Evidence**:
```
LEGIARTI000034596929 | III : Opérations exonérées | number=261 | score=3.1
LEGIARTI000036426360 | III : Opérations exonérées | number=261 | score=3.1
LEGIARTI000033971428 | III : Opérations exonérées | number=261 | score=3.1
... (14 identical-content versions)
```

**Fix**: Use `DISTINCT ON (t.category, t.number)` for LEGI FTS queries. The `category` field (LEGITEXT code) + `number` field uniquely identifies the logical article across versions. Only the highest-scoring version is kept.

For JADE and BOFIP, use `DISTINCT ON (t.doc_id)` for chunk-level dedup (each doc may have multiple chunks).

**Rationale**: The `(category, number)` pair groups all temporal versions of an article. Keeping only the best-scoring version per logical article ensures diverse results — 14 slots for 14 *different* articles, not 14 versions of the same one.

**File**: `web/services/vector_search.py` — `_fts_search()`

---

### 3. Document Length Normalization

**Problem**: Very long articles (15,000-17,000 chars, e.g., article 261 at ~17k chars) contain many common words by pure volume, scoring disproportionately high in `ts_rank_cd`.

**Fix**: Use `ts_rank_cd(chunk_tsv, query, 1)` — normalization flag `1` divides rank by `1 + log(document_length)`.

**Rationale**: Short, focused articles about "rémunérations des gérants" (article 62 at 1,593 chars) are penalized less than encyclopedic articles that happen to mention the same terms in passing. This rebalances scoring toward topical precision.

**File**: `web/services/vector_search.py` — `_fts_search()`

---

### 4. Reranker Max Length: 512 → 1024

**Problem**: `RERANKER_MAX_LENGTH=512` meant the cross-encoder (BAAI/bge-reranker-v2-m3) only saw the first ~375 words of each chunk. For 7,500-char chunks, the substantive legal content often starts after headers/metadata, meaning the reranker judged relevance on boilerplate.

**Fix**: Changed default to `1024` tokens (env-overridable). The model supports up to 8,192.

**Rationale**: At 1024 tokens (~750 words), the reranker sees enough substantive content to judge relevance. Going higher (2048+) would double inference time per pair, and with 40 pairs per query, latency matters. 1024 is the sweet spot.

**File**: `config/config.py` — `RERANKER_MAX_LENGTH`

---

### 5. Reranker Score Propagation (Sigmoid Normalization)

**Problem**: After reranking, results kept their original vector cosine similarity. The `min_confidence` filter used the wrong signal — a document with high cosine similarity but low reranker relevance would pass through.

**Fix**: `_cross_encoder_rerank()` now replaces `result.similarity` with the sigmoid-normalized reranker score: `1 / (1 + exp(-score))`.

**Rationale**: The cross-encoder returns raw logits (unbounded). Sigmoid maps them to [0, 1] where 0.5 = neutral, >0.5 = relevant, <0.5 = irrelevant. This aligns with `min_confidence` semantics. Scores are clamped to [-500, 500] before `exp()` to prevent overflow.

**File**: `web/services/retrieval.py` — `_cross_encoder_rerank()`

---

### 6. Reranker Minimum Score Threshold

**Problem**: Even clearly irrelevant documents (reranker score near 0) were returned because there was no score-based filtering on reranker output.

**Fix**: Added `RERANKER_MIN_SCORE=0.01` (env-overridable). Results below this threshold are filtered out after reranking.

**Rationale**: With sigmoid normalization, a score of 0.01 corresponds to a raw logit of ~-4.6, meaning the reranker is very confident the document is irrelevant. This conservative threshold only removes truly garbage results. Can be raised to 0.1-0.2 after empirical testing.

**File**: `config/config.py`, `web/services/retrieval.py`

---

### 7. Reciprocal Rank Fusion (RRF) for Hybrid Search

**Problem**: Vector search alone misses documents when the user's vocabulary differs from the corpus (e.g., "EURL" vs "société à responsabilité limitée").

**Fix**: Fuse vector and FTS result lists using RRF:
```
score(doc) = sum_over_lists( weight_i / (k + rank_i + 1) )
```

**Configuration**:
- `ENABLE_HYBRID_SEARCH=true` — toggle hybrid on/off
- `RRF_K=60` — smoothing constant (higher = more uniform blending)
- `FTS_WEIGHT=1.0` — relative weight of FTS vs vector (1.0 = equal)

**Rationale**: RRF is rank-based (not score-based), making it robust to incomparable score scales between vector similarity and FTS rank. A document appearing in BOTH lists gets boosted; one appearing in only FTS still contributes. The reranker downstream makes the final relevance decision.

**File**: `web/services/vector_search.py` — `_reciprocal_rank_fusion()`

---

### 8. Savepoint-Based Error Handling in FTS

**Problem**: If the `chunk_tsv` column doesn't exist yet (migration not run), `_fts_search` would fail and `conn.rollback()` would abort the entire transaction, breaking subsequent vector search queries on the same connection.

**Fix**: Wrap each per-table FTS query in a PostgreSQL `SAVEPOINT`. On error, `ROLLBACK TO SAVEPOINT` recovers the transaction state without affecting other work on the connection.

**Rationale**: The FastAPI dependency provides a shared connection per request. A full rollback would corrupt state for the entire request. Savepoints provide per-statement isolation.

**File**: `web/services/vector_search.py` — `_fts_search()`

---

### 9. Database Schema: Trigger-Based tsvector (not GENERATED STORED)

**Problem**: `ALTER TABLE ADD COLUMN ... GENERATED ALWAYS AS (...) STORED` forces PostgreSQL to rewrite the entire table in a single operation. For LEGI (~12k rows with large text), this exceeded shared memory limits:
```
could not resize shared memory segment to 2144474624 bytes: No space left on device
```

**Fix**: Use a plain `tsvector` column + `BEFORE INSERT OR UPDATE` trigger + batched backfill (5,000 rows per commit).

**Rationale**: The trigger approach is functionally equivalent (auto-populates on INSERT/UPDATE) but doesn't require a full-table rewrite. Batched backfill stays within shared memory limits by committing frequently.

**Files**: `database/database_manage.py` — `_ensure_fts_column()`, `database/sql_scripts/add_fts_columns.sql`

---

## Key Findings from Corpus Analysis

### Term Coverage

| Term | LEGI | JADE | BOFIP |
|------|------|------|-------|
| "EURL" (literal) | 0 | present in 29 docs | 3 |
| "SARL" (literal) | 0 | present in 29 docs | 3 |
| "rémunération" | 1,416 | present | present |
| "dirigeant" | 580 | present | present |
| "gérant" (synonym) | present | present | present |
| "société à responsabilité limitée" | present | present | present |

**Key insight**: LEGI never uses the acronyms "EURL"/"SARL" — it always uses the full legal form. FTS alone cannot bridge this vocabulary gap. Vector/semantic search handles it via embedding similarity, and the cross-encoder reranker makes the final discrimination.

### Article 62 CGI (Target Document)

- `doc_id`: LEGIARTI000006307049
- Contains: "rémunérations", "gérants majoritaires", "sociétés à responsabilité limitée", "associés"
- Does NOT contain: "dirigeant", "EURL", "SARL", "unique"
- FTS OR-score: 0.4 (matches 2 of 6 query terms)
- Embedding neighbors: correctly clusters with "Rémunérations allouées aux gérants et associés" (sim > 0.86)

**Conclusion**: For this query, vector search is the primary path to finding article 62. FTS adds value for queries where the user's terms appear verbatim in relevant documents.

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `ENABLE_HYBRID_SEARCH` | `true` | Enable FTS + vector + sparse fusion |
| `RRF_K` | `60` | RRF smoothing constant |
| `FTS_WEIGHT` | `1.0` | FTS weight relative to vector in RRF |
| `FTS_MODE` | `auto` | FTS strategy: `auto`, `and`, `or` |
| `ENABLE_QUERY_EXPANSION` | `true` | Expand legal acronyms and synonyms |
| `ENABLE_SPARSE_SEARCH` | `true` | Enable BGE-M3 sparse retrieval path |
| `SPARSE_WEIGHT` | `1.0` | Sparse weight relative to vector in RRF |
| `RERANKER_MODEL` | `BAAI/bge-reranker-v2-m3` | Cross-encoder model |
| `RERANKER_MAX_LENGTH` | `1024` | Max tokens per document for reranker |
| `RERANKER_MIN_SCORE` | `0.01` | Minimum sigmoid score to keep a result |
| `RETRIEVAL_OVERSAMPLING_FACTOR` | `4` | Candidates = top_k × factor |

---

## Additional Improvements (Implemented)

### 10. Query Expansion via Static Legal Synonym Map

**Problem**: "EURL" doesn't appear in LEGI (only "entreprise unipersonnelle à responsabilité limitée"), "dirigeant" doesn't match "gérant".

**Fix**: Static dictionary in `web/services/query_expansion.py` maps:
- Legal acronyms: EURL, SARL, SAS, SA, SCI, CGI, TVA, IR, IS, BIC, BNC, etc. → full form
- Common synonyms: dirigeant↔gérant, salaire↔rémunération, impôt↔imposition, etc.

`expand_query(text)` appends expansions to the original query before both embedding and FTS.

**Rationale**: Zero latency (static dict lookup), bridges vocabulary gap for both vector and FTS paths without requiring an LLM call. Covers ~50 legal acronyms and ~25 synonym pairs.

**Config**: `ENABLE_QUERY_EXPANSION=true`

**File**: `web/services/query_expansion.py`

---

### 11. Title/Metadata Boosting in FTS

**Problem**: FTS only indexed `chunk_text`. Article 62's sibling has title "Rémunérations allouées aux gérants et associés" which should boost ranking.

**Fix**: tsvector trigger now builds weighted composite:
```sql
setweight(to_tsvector('french', coalesce(NEW.title, '')), 'A') ||
setweight(to_tsvector('french', coalesce(NEW.chunk_text, '')), 'B')
```

`ts_rank_cd` automatically weights title matches 2.5× higher than body matches (A=1.0, B=0.4 default PostgreSQL weights).

**Rationale**: Titles are short and highly indicative of article topic. A title match is far more informative than a body match in a 7,500-char chunk.

**Migration**: Re-run `add_fts_columns` to regenerate tsvectors with title weighting.

**Files**: `database/database_manage.py`, `database/sql_scripts/add_fts_columns.sql`

---

### 12. Adaptive AND/OR FTS Strategy

**Problem**: OR is always used. For short precise queries ("article 62 CGI"), AND would give better precision.

**Fix**: `FTS_MODE=auto` (default):
- Short queries (≤3 words): try AND first; if zero results, fall back to OR
- Long queries (4+ words): use OR directly

**Config**: `FTS_MODE` with values `"auto"`, `"and"`, `"or"`

**Rationale**: Short queries are usually precise lookups where AND is correct (user expects all terms present). Long queries are exploratory where OR provides recall. Auto mode adapts without user intervention.

**File**: `web/services/vector_search.py` — `_fts_search()`

---

### 13. BGE-M3 Sparse Retrieval (Full Implementation)

**Problem**: PostgreSQL's French stemmer is rule-based and lossy. BGE-M3's learned sparse embeddings provide superior lexical matching.

**Architecture**: 3-way RRF fusion: dense (pgvector) + FTS (tsvector) + sparse (JSONB)

**Fix**:
- New service `web/services/sparse_embedding.py` using FlagEmbedding's `BGEM3FlagModel`
- New `sparse_embedding JSONB` column on all tables (stores top-256 weighted tokens)
- GIN index for fast `?|` operator (key overlap)
- `_sparse_search()` fetches candidates by token overlap, computes dot product in Python
- 3-way RRF fusion in `vector_search()` with configurable `SPARSE_WEIGHT`

**Config**: `ENABLE_SPARSE_SEARCH=true`, `SPARSE_WEIGHT=1.0`

**Migration**: Run `add_sparse_embeddings` CLI command to backfill (batches of 256).

**Files**: `web/services/sparse_embedding.py`, `web/services/vector_search.py`, `main.py`

---

## CLI Commands

```bash
# Add FTS columns to an existing database (run once after deployment)
mediatech add_fts_columns
# Or: python main.py add_fts_columns

# Generate sparse embeddings (requires FlagEmbedding, run after add_fts_columns)
mediatech add_sparse_embeddings
# Or: python main.py add_sparse_embeddings
```

`add_fts_columns`:
1. Adds `chunk_tsv tsvector` column to LEGI, JADE, BOFIP
2. Creates GIN indexes for fast `@@` matching
3. Creates `BEFORE INSERT OR UPDATE` triggers (weighted: title=A, chunk_text=B)
4. Backfills existing rows in batches of 5,000 (commits after each batch)

`add_sparse_embeddings`:
1. Adds `sparse_embedding JSONB` column to LEGI, JADE, BOFIP
2. Creates GIN indexes for fast `?|` (key overlap) matching
3. Encodes all documents via BGE-M3 FlagEmbedding (batches of 256)
4. Stores top-256 weighted tokens per document

---

## Files Modified

| File | Change |
|------|--------|
| `config/config.py` | Added `RERANKER_MIN_SCORE`, `ENABLE_HYBRID_SEARCH`, `RRF_K`, `FTS_WEIGHT`, `ENABLE_QUERY_EXPANSION`, `FTS_MODE`, `ENABLE_SPARSE_SEARCH`, `SPARSE_WEIGHT`; bumped `RERANKER_MAX_LENGTH` to 1024 |
| `config/__init__.py` | Exported new config values |
| `web/services/query_expansion.py` | **NEW** — static legal acronym/synonym dictionary with `expand_query()` |
| `web/services/sparse_embedding.py` | **NEW** — FlagEmbedding BGE-M3 wrapper for sparse retrieval |
| `web/services/vector_search.py` | Added `_fts_search()` (adaptive AND/OR, deduplicated, length-normalized), `_sparse_search()`, `_reciprocal_rank_fusion()`, 3-way RRF hybrid dispatcher with query expansion |
| `web/services/retrieval.py` | Sigmoid-normalized reranker scores with overflow protection, added `RERANKER_MIN_SCORE` filter |
| `database/database_manage.py` | Added `_ensure_fts_column()` (title-weighted trigger), `_ensure_sparse_column()` |
| `database/sql_scripts/add_fts_columns.sql` | Migration script with title-weighted tsvector + sparse JSONB column |
| `main.py` | Added `add_fts_columns` and `add_sparse_embeddings` CLI commands |
| `pyproject.toml` | Added `FlagEmbedding>=1.2.0` dependency |
| `.env.example` | Documented new environment variables |
