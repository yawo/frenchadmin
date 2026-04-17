# Copilot Instructions for MEDIATECH

## Project Overview

MEDIATECH is a Python data pipeline project that downloads, processes, embeds, and stores public data made available by French government agencies (e.g., service-public.fr, LEGI, CNIL, travail-emploi, DOLE, constitutional texts). The processed data is vectorized and stored in a PostgreSQL/PgVector database, then exported to Parquet and published on Hugging Face for use in RAG and AI applications.

## Tech Stack

- **Language**: Python 3.10+
- **CLI**: [`docopt`](http://docopt.org/) — entry point is `main.py`, installed as the `mediatech` command
- **Orchestration**: Apache Airflow (DAGs in `airflow_config/dags/`)
- **Database**: PostgreSQL with the `pgvector` extension (via `psycopg2-binary`)
- **Embeddings**: OpenAI-compatible API client (`openai`) pointing to the Albert API (`albert.api.etalab.gouv.fr`); tokenizers loaded via `transformers`
- **Data processing**: Polars, Pandas, DuckDB, lxml, BeautifulSoup4
- **Export/upload**: Parquet files via Polars, published to Hugging Face Hub
- **Linting**: `ruff` (dev dependency)
- **CI/CD**: GitHub Actions (`.github/workflows/ci_cd.yml`) + Google Cloud Build + SSH deployment

## Project Structure

```
.
├── main.py                        # CLI entry point (docopt)
├── pyproject.toml                 # Project metadata and dependencies
├── config/
│   ├── config.py                  # Env vars, paths, source mapping (SOURCE_MAP)
│   ├── data_config.json           # List of data sources to download/process
│   └── logging_config.py         # Logging setup
├── database/                      # Table creation, data insertion, export
├── download_and_processing/       # Source-specific download and processing logic
├── utils/                         # Shared utilities (HuggingFace upload, etc.)
├── airflow_config/
│   └── dags/                      # Airflow DAG definitions (one per source + full_pipeline)
├── scripts/                       # Shell scripts for deployment, backup, updates
├── docs/                          # Documentation and Jupyter notebooks
└── .github/workflows/ci_cd.yml   # GitHub Actions pipeline
```

## Key Conventions

- **Source names** are defined in `config/config.py` under `SOURCE_MAP`. Valid values: `service_public`, `travail_emploi`, `legi`, `cnil`, `state_administrations_directory`, `local_administrations_directory`, `constit`, `dole`, `data_gouv_datasets_catalog`.
- **Embedding model**: default is `louisbrulenaudet/lemone-embed-pro`; always pass `--model` consistently across commands since it determines the vector dimension.
- **Environment variables**: copy `.env.example` to `.env` and fill in values before running. Key vars: `POSTGRES_*`, `QDRANT_*`, `API_KEY`, `HF_TOKEN`, `EMBEDDING_MODEL`.
- **Docker vs. local**: `config.py` detects `RUNNING_IN_DOCKER=true` and uses absolute paths (`/tmp/mediatech`); locally it uses relative paths from the project root.
- **Linting**: run `ruff check .` and `ruff format .` before submitting changes.

## Development Setup

```bash
# Install system dependencies (Ubuntu/Debian)
sudo apt-get update
sudo apt-get install -y $(cat config/requirements-apt-container.txt)

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install the project in editable mode (makes `mediatech` command available)
pip install -e .

# Verify installation
mediatech --help
python -c "import config; import database; import download_and_processing; print('All imports OK')"
```

## Running the Pipeline (CLI)

```bash
# Create PostgreSQL tables
mediatech create_tables --model louisbrulenaudet/lemone-embed-pro

# Download all sources
mediatech download_files --all

# Download + process all sources (more storage-efficient than separate steps)
mediatech download_and_process_files --all --model louisbrulenaudet/lemone-embed-pro

# Process a single source
mediatech process_files --source service_public --model louisbrulenaudet/lemone-embed-pro

# Export to Parquet
mediatech export_table --table all --output data/parquet

# Upload to Hugging Face
mediatech upload_dataset --all --repository AgentPublic
```

## Adding a New Data Source

1. Add the source name and its table list to `SOURCE_MAP` in `config/config.py`.
2. Add a data folder constant (e.g., `MY_SOURCE_DATA_FOLDER`) in `config/config.py`.
3. Create a downloader in `download_and_processing/` following existing patterns.
4. Create a processor in `download_and_processing/` following existing patterns.
5. Register the processor in `download_and_processing/__init__.py`.
6. Add a corresponding Airflow DAG in `airflow_config/dags/`.
7. Add the source entry to `config/data_config.json`.

## Testing & Linting

```bash
# Lint with ruff
pip install ruff
ruff check .
ruff format --check .

# Validate imports (mirrors the CI check)
python -c "import config; import database; import download_and_processing; print('All imports OK')"
mediatech --help
```

There is no automated unit test suite currently. Validation is done via import checks and CLI smoke tests (see `.github/workflows/ci_cd.yml`).

## CI/CD

- **`validate` job**: installs dependencies, runs import checks and `mediatech --help`.
- **`deploy-cloud-run` job**: triggers Google Cloud Build on pushes to `main` (requires `GCP_*` secrets/vars).
- **`deploy` job**: SSHes into the deployment VM and runs `containers_deployment.sh` (requires `VM_HOST`, `VM_USER`, `SSH_PRIVATE_KEY`, `VM_PROJECT_DIR` secrets).

## Important Notes

- Never commit `.env` files or secrets. Use `.env.example` as a reference.
- The `data/` directory (downloaded files, parquet exports) is git-ignored.
- Airflow logs and backup files are also git-ignored.
- When modifying DAGs, ensure the Airflow container is restarted via `./scripts/containers_deployment.sh`.
- The `full_pipeline` DAG manages the execution order of all individual source DAGs.
