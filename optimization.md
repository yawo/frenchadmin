# Optimization Plan Before Any Rust Migration

## Purpose
This document defines the highest-value optimization work to improve throughput, latency, and reliability of the current Python pipeline before considering a Rust migration.

It is designed to be directly actionable by an AI coding agent with minimal additional context.

## Scope
This plan covers exactly 5 optimization tracks:

1. Batch embeddings more aggressively
2. Improve PostgreSQL insert strategy
3. Reduce FalkorDB graph round-trips
4. Parallelize document processing at process level
5. Add profiling and telemetry gates

## Non-goals
- No full language migration
- No schema redesign beyond what is strictly needed for performance
- No change in business semantics (same documents, same metadata, same graph relations)

## Baseline Requirements
Before implementation:
- Keep behavior-compatible outputs (rows, key fields, links, graph relations)
- Keep checkpoint/restart behavior where already present
- Keep failure handling robust (no silent data corruption)
- Add timing and throughput logs for each stage

---

## System Context (Current Hot Path)
Primary flow:
- File selection and extraction: download_and_processing/smart_process_tax.sh
- Parsing and chunking: download_and_processing/files_processing.py
- Embedding generation: utils/chunking_and_embedding.py
- PostgreSQL writes: database/database_manage.py (insert_data)
- Graph upserts: database/graph_manage.py

Observed bottleneck classes:
- Embedding compute (local SentenceTransformer)
- Per-doc database round-trips
- Graph query fanout
- Sequential orchestration in Python loops
- Weak stage-level timing visibility

---

## Optimization 1: Batch Embeddings More Aggressively

### Objective
Increase embedding throughput by reducing per-call overhead and maximizing model batch efficiency.

### Current Issues
- Embeddings are often generated per chunk in tight loops in download_and_processing/files_processing.py.
- This causes repeated Python overhead and suboptimal model utilization.

### Implementation Steps
1. Add a helper that embeds a list of chunk_text values in one call.
2. In each processing loop (LEGI, JADE, BOFiP), build chunk_text for all chunks first.
3. Call generate_embeddings_with_retry once per document or per bounded micro-batch.
4. Zip returned embeddings back to chunk rows by index.
5. Keep fallback behavior for partial failures:
   - If a large batch fails repeatedly, split into smaller batches (binary split strategy).

### Code Touchpoints
- utils/chunking_and_embedding.py
- download_and_processing/files_processing.py

### Suggested API
- New utility function:
  - name: embed_texts_with_retry
  - input: list[str], model, attempts
  - output: list[list[float]]
  - behavior: retries as a batch, with optional split-on-failure.

### Acceptance Criteria
- For large inputs, embedding calls per N chunks reduced by at least 5x.
- End-to-end throughput increase at least 20% on representative datasets.
- No change in row counts or doc_id/chunk_id mapping.

### Validation
- Compare before/after:
  - chunks/sec
  - embedding stage wall time
  - error rate
- Verify deterministic row mapping by checking chunk_id -> embedding presence.

---

## Optimization 2: Improve PostgreSQL Insert Strategy

### Objective
Reduce insertion latency and lock overhead by replacing expensive write patterns.

### Current Issues
- insert_data uses executemany with per-document delete-then-upsert.
- This creates many round-trips and unnecessary churn.

### Implementation Steps
1. Replace cursor.executemany with psycopg2.extras.execute_values where feasible.
2. Add configurable page_size (start with 500 to 2000 rows).
3. For large batches, evaluate COPY into temporary table + merge/upsert.
4. Avoid unconditional per-doc delete if ON CONFLICT covers correctness.
   - If delete is required for business semantics, do one delete per doc_id set, not per row.
5. Wrap each logical batch in one transaction.

### Code Touchpoints
- database/database_manage.py (insert_data)

### Safety Constraints
- Preserve current upsert semantics for all supported tables.
- Do not break model-specific embedding column naming.

### Acceptance Criteria
- Insert stage latency reduced at least 30%.
- DB CPU usage lower or stable at same throughput.
- Zero increase in duplicate rows.

### Validation
- Add counters:
  - rows inserted
  - rows updated
  - commit duration
- Run SQL checks:
  - duplicate chunk_id count must remain zero
  - expected doc_id coverage unchanged

---

## Optimization 3: Reduce FalkorDB Graph Round-Trips

### Objective
Decrease graph update latency by consolidating many small Cypher calls into fewer batched operations.

### Current Issues
- graph_manage currently performs multiple _safe_query calls per document and relation.
- Network and query overhead dominate when many links exist.

### Implementation Steps
1. Keep doc-level grouping by doc_id.
2. Build one parameterized query per document type (LEGI/JADE/BOFiP) that:
   - upserts node properties
   - unwinds relationship targets for REFERENCES
   - creates BELONGS_TO_CODE/ISSUED_BY/DECIDED_BY as needed
3. Use UNWIND with parameter arrays for target IDs.
4. Deduplicate target IDs in Python before query submission.
5. Keep best-effort non-fatal behavior.

### Code Touchpoints
- database/graph_manage.py

### Query Strategy
- Replace loops of many MERGE calls with one MERGE + UNWIND query per doc.

### Acceptance Criteria
- Graph stage time reduced at least 40% for relation-heavy documents.
- No loss in node or relationship counts.

### Validation
- Compare graph counts before/after:
  - nodes by label
  - relationships by type
- Sample consistency checks on known doc_id references.

---

## Optimization 4: Parallelize Document Processing at Process Level

### Objective
Improve pipeline throughput by using controlled multi-process parallelism for parse/chunk/embed/prepare stages.

### Current Issues
- Processing in files_processing is mostly sequential.
- CPU and model resources are underutilized on multi-core machines.

### Implementation Steps
1. Introduce a bounded process pool for document-level units.
2. Separate stages into:
   - parallel-safe transform work (parse/chunk/chunk_text prep)
   - serialized writes (DB insert and graph update) or controlled writer pool
3. Add worker-safe result envelope:
   - success payload with rows
   - failure payload with source identifier and error
4. Keep checkpoint semantics deterministic:
   - commit checkpoint only after successful write of result payload.
5. Add config flags:
   - MAX_WORKERS
   - BATCH_SIZE_DOCS
   - WRITE_CONCURRENCY

### Code Touchpoints
- download_and_processing/files_processing.py
- optional: config/config.py for tunables

### Concurrency Rules
- Do not share mutable global model objects across processes blindly.
- If embedding model is too heavy to load per process, use fewer workers and larger batches.

### Acceptance Criteria
- End-to-end throughput improves at least 1.5x on >=8 core machine.
- No checkpoint corruption.
- No increase in failed docs under normal load.

### Validation
- Run with workers 1, 2, 4, 8 and compare:
  - docs/sec
  - memory usage
  - failure rate
- Verify exactly-once behavior for chunk_id keys.

---

## Optimization 5: Add Profiling and Telemetry Gates

### Objective
Make performance measurable and regressions detectable before and after each optimization.

### Current Issues
- Stage-level timing is incomplete and inconsistent.
- Hard to attribute slowdowns.

### Implementation Steps
1. Add structured timing around major stages:
   - smart preprocessing
   - parse
   - chunking
   - embedding
   - postgres insert
   - graph upsert
2. Emit per-batch metrics:
   - docs processed
   - chunks produced
   - rows written
   - retries
   - errors
3. Add cumulative run summary at end.
4. Add optional profiling mode:
   - cProfile for CPU hotspots
   - memory snapshots for large runs
5. Define performance gates in CI or release checklist:
   - baseline comparison over fixed sample set
   - fail gate on >10% regression in key metrics

### Code Touchpoints
- download_and_processing/files_processing.py
- database/database_manage.py
- database/graph_manage.py
- utils/chunking_and_embedding.py

### Acceptance Criteria
- Every run logs stage times and throughput.
- Team can identify top bottleneck from logs alone.
- Regression alerts trigger on threshold breach.

### Validation
- Produce one benchmark report JSON per run.
- Include host info, model, dataset sample, and config flags.

---

## Execution Order (Recommended)
Implement in this order:

1. Optimization 5 (telemetry first)
2. Optimization 1 (embedding batching)
3. Optimization 2 (DB strategy)
4. Optimization 3 (graph batching)
5. Optimization 4 (parallelism)

Reason: visibility first, then biggest low-risk wins, then concurrency complexity.

---

## Benchmark Protocol
Use a fixed benchmark sample for repeatability:
- A representative subset of selected LEGI, JADE, BOFiP inputs
- Same model and hardware profile for all runs
- Warm-up run excluded
- At least 3 measured runs per configuration

Record:
- total wall time
- docs/sec
- chunks/sec
- embedding stage time
- DB stage time
- graph stage time
- peak memory
- error/retry counts

---

## Rollback Plan
For each optimization, keep a feature flag to disable quickly:
- ENABLE_BATCH_EMBEDDING
- ENABLE_FAST_DB_INSERT
- ENABLE_BATCH_GRAPH_UPSERT
- ENABLE_PARALLEL_PROCESSING
- ENABLE_PERF_TELEMETRY

If regressions appear, disable only the offending optimization and keep others active.

---

## Implementation Checklist for AI Agent

1. Add telemetry wrappers and run summary output.
2. Refactor embedding calls to batched API.
3. Refactor insert_data with execute_values and transactional batching.
4. Refactor graph upsert methods to single-query + UNWIND strategy.
5. Add process pool orchestration with deterministic checkpoint commits.
6. Add feature flags and defaults.
7. Add benchmark script and baseline report output.
8. Validate data correctness and performance gates.
9. Document tuning guidance in README.

---

## Definition of Done
- All 5 optimizations implemented behind flags.
- Correctness parity confirmed on benchmark sample.
- End-to-end performance improved by at least 2x on representative workload, or a clear measured breakdown showing remaining external bottlenecks.
- No critical reliability regressions.
- Benchmark and telemetry artifacts generated and stored.
