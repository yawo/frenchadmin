# Tech Stack

## Language & Runtime

- Python ≥ 3.10
- Package managed via `pyproject.toml` (setuptools + wheel)
- Installed in editable mode: `pip install -e .`
- CLI entry point: `mediatech` (maps to `main:main` via `project.scripts`)

## Key Libraries

| Category | Library |
|---|---|
| CLI parsing | `docopt` |
| HTTP / scraping | `requests`, `beautifulsoup4`, `lxml` |
| Embeddings (local) | `sentence-transformers`, `fastembed`, `transformers` |
| Text splitting | `langchain-text-splitters`, `langchain` |
| PostgreSQL | `psycopg2-binary` (connection pool via `psycopg2.pool.ThreadedConnectionPool`) |
| Vector DB | `qdrant-client` |
| Graph DB | `falkordb` |
| Data processing | `polars`, `pandas`, `duckdb` |
| Parquet / export | `polars`, `duckdb` |
| Hugging Face | `huggingface-hub` |
| Hashing | `xxhash` (chunk deduplication) |
| Pipeline orchestration | `apache-airflow` (v3), `apache-airflow-providers-apprise` |
| Notifications | Apprise via Airflow (Tchap/Matrix) |
| Linting | `ruff` (dev dependency) |

## Databases & Services

| Service | Image / Version | Default local port |
|---|---|---|
| PostgreSQL + pgvector | `pgvector/pgvector:pg16` | 5433 |
| Qdrant | `qdrant/qdrant:v1.17-unprivileged` | 6333 |
| FalkorDB | `falkordb/falkordb:latest` | 6379 |
| Airflow API server | custom build (see `Dockerfile`) | 8080 |

All services are orchestrated via `docker-compose.yml`. The Airflow image is built from the project `Dockerfile` which installs system deps and the `mediatech` package.

## Environment Configuration

All secrets and paths are loaded from a `.env` file (see `.env.example`). The `config/config.py` module reads them via `python-dotenv` and exposes typed constants. It auto-detects Docker vs. local execution via the `RUNNING_IN_DOCKER` env var and adjusts hostnames and base paths accordingly.

Key env vars:
- `EMBEDDING_MODEL` — HuggingFace model identifier (default: `louisbrulenaudet/lemone-embed-pro`)
- `POSTGRES_*` — database connection
- `QDRANT_*` / `FALKORDB_*` — optional vector/graph stores
- `HF_TOKEN` — Hugging Face upload token
- `API_KEY` / `API_URL` / `LLM_MODEL` — OpenRouter LLM API (optional)
- `AIRFLOW_UID` — must match `id -u` on the host

## Common Commands

### Local CLI

```bash
# Install
pip install -e .

# Run full pipeline for one source
mediatech download_and_process_files --source legi --model louisbrulenaudet/lemone-embed-pro

# Run full pipeline for all sources
mediatech download_and_process_files --all --model louisbrulenaudet/lemone-embed-pro

# Create/reset DB tables
mediatech create_tables --model louisbrulenaudet/lemone-embed-pro
mediatech create_tables --model louisbrulenaudet/lemone-embed-pro --delete-existing

# Export to Parquet
mediatech export_table --table all --output data/parquet

# Upload to Hugging Face
mediatech upload_dataset --all --repository AgentPublic

# Full pipeline script (download → create tables → process → export → upload)
./scripts/update.sh
```

### Docker / Airflow

```bash
# Initial server setup (installs Docker etc.)
./scripts/initial_deployment.sh

# Build and start all containers
./scripts/containers_deployment.sh

# Export .env vars then start only the DB
export $(grep -v '^#' .env | xargs)
docker compose up -d postgres

# Get Airflow JWT token
curl -X POST http://localhost:8080/auth/token \
  -H 'Content-Type: application/json' \
  -d "{\"username\": \"${_AIRFLOW_WWW_USER_USERNAME}\", \"password\": \"${_AIRFLOW_WWW_USER_PASSWORD}\"}"

# Set pipeline schedule inside the scheduler container
docker exec -it airflow-scheduler airflow variables set full_pipeline_schedule "0 19 * * 5"
```

### Maintenance

```bash
# Backup pgvector volume
./scripts/backup.sh

# Restore from backup
./scripts/restore.sh

# Delete old logs/backups (keeps last N days per RETENTION_DAYS env var)
./scripts/delete_old_files.sh
```

## Linting

```bash
ruff check .
ruff format .
```

No test suite is currently defined in the project.
