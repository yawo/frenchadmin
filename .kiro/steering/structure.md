# Project Structure

## Top-Level Layout

```
mediatech/
├── main.py                        # CLI entry point (docopt-based)
├── pyproject.toml                 # Project metadata and dependencies
├── Dockerfile                     # Airflow container image
├── docker-compose.yml             # Full stack: Airflow + PostgreSQL + Qdrant + FalkorDB
├── .env / .env.example            # Environment variables (never commit .env)
│
├── config/                        # Configuration and constants
├── database/                      # DB table management and graph layer
├── download_and_processing/       # Download + ETL logic per source
├── utils/                         # Shared utilities
│
├── airflow_config/
│   └── dags/                      # One DAG file per data source + full_pipeline.py
│
├── data/
│   ├── unprocessed/               # Raw downloaded files (one subfolder per source)
│   └── parquet/                   # Exported Parquet files ready for HF upload
│
├── scripts/                       # Shell scripts for deployment, backup, maintenance
├── logs/                          # Script execution logs (date-stamped)
├── backups/                       # pgvector volume backups
├── docs/                          # Jupyter notebooks and documentation
└── tmp/                           # Temporary working files
```

## Module Descriptions

### `config/`
- `config.py` — All environment-derived constants (`POSTGRES_*`, `QDRANT_*`, `FALKORDB_*`, `EMBEDDING_MODEL`, `SOURCE_MAP`, data folder paths). Import constants from here; never read `os.getenv` directly in other modules.
- `logging_config.py` — `setup_logging(debug=False)` and `get_logger(name)`. Always use `get_logger(__name__)` for module-level loggers.
- `data_config.json` — Defines download URLs, folder paths, and source types for each dataset.
- `data_history.json` — Tracks the last downloaded file per source (mutable at runtime).

### `database/`
- `database_manage.py` — PostgreSQL operations: table creation (with pgvector HNSW indexes), connection pool (`ThreadedConnectionPool`), `refresh_table` context manager, export to Parquet via DuckDB, `split_legi_table`.
- `graph_manage.py` — FalkorDB knowledge graph layer (best-effort; silently disabled if FalkorDB is unavailable). Provides `upsert_legi_node`, `upsert_jade_node`, `upsert_bofip_node`.
- `sql_scripts/init.sql` — PostgreSQL initialisation script (run once by the pgvector container).

### `download_and_processing/`
- `download_files.py` — `download_and_optionally_process_files(table_name, process, ...)` and `download_and_optionally_process_all_files(...)`. Handles multiple source types: `dila_folder`, `directory`, `sheets`, `data_gouv`, `bofip`.
- `files_processing.py` — `process_data(table_name, model, ...)` and `process_all_data(...)`. Reads raw files, chunks, embeds, and inserts into PostgreSQL (and optionally Qdrant/FalkorDB).

### `utils/`
- `chunking_and_embedding.py` — `make_chunks()`, `generate_embeddings()`, `generate_embeddings_with_retry()`, `CorpusHandler` ABC and `SheetChunksHandler`. Embedding models are cached in module-level dicts.
- `data_helpers.py` — File I/O helpers (`download_file`, `extract_and_remove_tar_file`), DB helpers (`_extract_distinct_data`, `correct_wrong_column_contents`), string formatters (`format_model_name`, `format_to_table_name`).
- `sheets_parser.py` — `RagSource` class for parsing XML-based service_public / travail_emploi sheets.
- `hugging_face.py` — `HuggingFace` class and `upload_dataset_task` for pushing Parquet files to the Hub.
- `checkpoint_manager.py` — Checkpoint utilities for resumable processing.

### `airflow_config/dags/`
Each DAG follows the same four-task pattern:
```
create_tables >> download_and_process_files >> export_table >> upload_dataset
```
- One file per source (e.g. `legi.py`, `bofip.py`, `service_public.py`).
- `full_pipeline.py` — Orchestrates all source DAGs in dependency order.
- DAG params (`model`, `repository`, `private`, `split`) can be overridden at trigger time.
- Tchap notifications via `on_execute_callback`, `on_success_callback`, `on_failure_callback` (requires `TchapNotifier` Airflow connection).

## Key Conventions

### Adding a New Data Source
1. Add an entry to `config/data_config.json` with `download_url`, `download_folder`, and `type`.
2. Add the source key to `SOURCE_MAP` in `config/config.py` and define its data folder constant.
3. Add a `CREATE TABLE` branch in `database/database_manage.py` → `create_all_tables()`.
4. Add a processing branch in `download_and_processing/files_processing.py` → `process_data()`.
5. Create a DAG file in `airflow_config/dags/` following the existing four-task pattern.

### Database Schema Conventions
- Every table has: `chunk_id TEXT PRIMARY KEY`, `doc_id TEXT NOT NULL`, `chunk_xxh64 TEXT NOT NULL`, `chunk_text TEXT`, `embeddings_{model_name} vector(N)`.
- `chunk_id` is a UUID; `doc_id` groups all chunks of the same source document.
- `chunk_xxh64` is an xxHash-64 of `chunk_text` (seed 2025) used for deduplication.
- The embedding column name encodes the model: `format_model_name(model)` strips the `org/` prefix (e.g. `BAAI/bge-m3` → `bge-m3`). **The same model must be used consistently across `create_tables`, `process_files`, and queries.**
- Two indexes per table: HNSW on the embedding column (`vector_cosine_ops`, m=16, ef_construction=128) and B-tree on `doc_id`.

### Paths
- Never hardcode absolute paths. Use `BASE_PATH` from `config` and `os.path.join`.
- `BASE_PATH` is `/tmp/mediatech` in Docker, `.` locally.

### Error Handling
- Use `logger.error(...)` then `raise` to propagate exceptions up the call stack.
- FalkorDB and Qdrant operations are best-effort: wrap in try/except and log warnings, never let them interrupt the main ETL flow.
- Embedding generation uses `generate_embeddings_with_retry()` (5 attempts, 60 s sleep).

### Logging
- Always get a module logger with `logger = get_logger(__name__)` at module level.
- Call `setup_logging(debug=...)` once at application startup (done in `main.py`).
- Log files are written to `logs/` with date-stamped filenames.
