# FrenchAdmin

[![License](https://img.shields.io/github/license/etalab-ia/mediatech?label=licence&color=red)](https://github.com/etalab-ia/mediatech/blob/main/LICENSE)
[![French version](https://img.shields.io/badge/🇫🇷-French%20version-blue)](./docs/README_fr.md)
[![Hugging Face collection](https://img.shields.io/badge/🤗-Hugging%20Face%20collection-yellow)](https://huggingface.co/collections/AgentPublic/mediatech-68309e15729011f49ef505e8)


## 📝 Description

FrenchAdmin processes French public administration data for AI applications in the public sector, with a focus on **tax law** (BOFiP/ CGI). It downloads, processes, embeds, and stores data in PostgreSQL with PgVector for vector search, and FalkorDB for knowledge graph relationships.

Key capabilities:
- **LEGI**: French legislative texts (Code Général des Impôts, LPF, etc.)
- **JADE**: Judicial decisions from French courts
- **BOFiP**: Tax guidance documents (Bulletin Officiel des Finances Publiques)
- **Cross-reference inference**: Automatic linking between JADE/BOFiP and LEGI articles for RAG and graphRAG

## 💡 Get Started

### </> Use local CLI

#### Installing Dependencies

1. Install the required apt dependencies:
   ```bash
   sudo apt-get update
   sudo apt-get install -y $(cat config/requirements-apt-container.txt)
   ```

2. Create and activate a virtual environment:
   ```bash
   python3 -m venv .venv  # Create the virtual environment
   source .venv/bin/activate  # Activate the virtual environment
   ```

3. Install the required python dependencies:
   ```bash
   pip install -e .
   ```

> Installing in development mode (`-e`) allows you to use the `mediatech` command and modify the code without reinstalling.

> **Note:** Make sure your environment is properly configured before continuing.

#### Database Configuration (PostgreSQL + FalkorDB)

1. Set up the environment variables in a [`.env`](.env) file based on the example in [`.env.example`](.env.example).

2. Export [`.env`](.env) variables :
   ```bash
   export $(grep -v '^#' .env | xargs)
   ```

3. Start the containers with Docker:
   ```bash
   docker compose up -d
   ```

4. Verify containers are running:
   ```bash
   docker ps
   ```

   You should see:
   - `pg` - PostgreSQL with PgVector (vector search)
   - `falkor` - FalkorDB (graph database)

#### Downloading, Processing and Uploading Data

##### Using the `mediatech` Command

After installation, the `mediatech` command is available globally and replaces `python main.py`:

> If you encounter issues with the `mediatech` command, you can still use `python main.py` instead.

The [`main.py`](main.py) file is the main entry point of the project and provides a command-line interface (CLI) to run each step of the pipeline separately.  
You can use it as follows:

```bash
mediatech <command> [options]
```
or 

```bash
python main.py <command> [options]
```

Command examples:
- View help:
  ```bash
  mediatech --help
  ```
- Create PostgreSQL tables:  
  ```bash
  mediatech create_tables --model BAAI/bge-m3
  ```
- Download all files listed in [`data_config.json`](config/data_config.json):  
  ```bash
  mediatech download_files --all
  ```
- Download files from the `legi` source:  
  ```bash
   mediatech download_files --source legi
  ```
- Download and process all files listed in [`data_config.json`](config/data_config.json):  
  ```bash
  mediatech download_and_process_files --all --model BAAI/bge-m3
  ```
- Process all data:  
  ```bash
  mediatech process_files --all --model BAAI/bge-m3
  ```
- Split a table into subtables based on different criteria (see [`main.py`](main.py)):  
  ```bash
  mediatech split_table --source legi
  ```
- Export PostgreSQL tables to parquet files:  
  ```bash
  mediatech export_tables --output data/parquet
  ```
- Upload parquet datasets to the Hugging Face repository:
  ```bash
   mediatech upload_dataset --input data/parquet/bofip.parquet --dataset-name bofip
  ```


Run `mediatech --help` in your terminal to see all available options, or check the code directly in [`main.py`](main.py).


##### Alternative Usage with `python main.py`

If you prefer to use the Python script directly, you can always use:

```bash
python main.py <command> [options]
```

Examples:
```bash
python main.py download_files
python main.py create_tables --model BAAI/bge-m3
python main.py process_files --all --model BAAI/bge-m3
```

### Performance and Optimization Flags

The processing pipeline now exposes optimization switches via environment variables:

```bash
export ENABLE_BATCH_EMBEDDING=true
export ENABLE_FAST_DB_INSERT=true
export ENABLE_BATCH_GRAPH_UPSERT=true
export ENABLE_PARALLEL_PROCESSING=false
export ENABLE_PERF_TELEMETRY=true
```

Tuning variables:

```bash
export EMBEDDING_BATCH_MAX_SIZE=64
export FAST_DB_INSERT_PAGE_SIZE=1000
export MAX_WORKERS=4
export BATCH_SIZE_DOCS=32
```

When telemetry is enabled, each run writes a JSON report in `data/perf_reports/`.

### Benchmark and Regression Gate

You can run the fixed-sample benchmark helper and enforce a regression gate:

```bash
python scripts/benchmark_pipeline.py \
   --command "python main.py process_files --source legi --model louisbrulenaudet/lemone-embed-pro" \
   --runs 3 \
   --run-prefix process_legi \
   --reports-dir data/perf_reports
```

Optional baseline gate (fails if runtime degrades by more than 10%):

```bash
python scripts/benchmark_pipeline.py \
   --command "python main.py process_files --source legi --model louisbrulenaudet/lemone-embed-pro" \
   --runs 3 \
   --run-prefix process_legi \
   --baseline data/perf_reports/process_legi_baseline.json \
   --regression-threshold 0.10
```

##### Using the [`update.sh`](update.sh) Script

The [`update.sh`](update.sh) script allows you to run the entire data processing pipeline: downloading, table creation, vectorization, and export.  
To run it, execute the following command from the project root:

```bash
./scripts/update.sh
```

This script will:
- Wait for the PostgreSQL database to be available,
- Create or update the necessary tables in the PostgreSQL database,
- Download public files listed in [`data_config.json`](config/data_config.json),
- Process and vectorize the data,
- Export the tables in Parquet format,
- Upload the Parquet files to [Hugging Face](https://huggingface.co/AgentPublic).

## 🗂️ Project Structure

- **[`main.py`](main.py)**: Main entry point with CLI for pipeline commands.
- **[`pyproject.toml`](pyproject.toml)**: Python project and dependency configuration.
- **[`Dockerfile`](Dockerfile)**: Docker image for containerized execution, installs system dependencies and project packages.
- **[`docker-compose.yml`](docker-compose.yml)**: Multi-container setup: PostgreSQL (PgVector) + FalkorDB.
- **[`.github/`](.github/)**: GitHub Actions workflows for CI/CD.
- **[`download_and_processing/`](download_and_processing/)**: Scripts to download and extract files from DILA (LEGI, JADE) and data.economie.gouv.fr (BOFiP).
- **[`database/`](database/)**: Database management (table creation, data insertion, FalkorDB graph operations).
- **[`docs/`](/docs/)**: Documentation and tutorials.
  - **[`docs/hugging_face_rag_tutorial.ipynb`](/docs/hugging_face_rag_tutorial.ipynb)**: RAG Tutorial: How to load datasets from Hugging Face and use them in a RAG pipeline ?
  - **[`docs/reconstruct_vector_database.ipynb`](/docs/reconstruct_vector_database.ipynb)**: Tutorial: How to reconstruct a dataset without chunking and embedding from parquet files?
  - **[`docs/fr/`](/docs/fr/)**: French translations of documentation.
- **[`utils/`](utils/)**: Shared utilities (chunking, embedding, HuggingFace, telemetry).
- **[`config/`](config/)**: Project configuration (data sources, embedding models, optimization flags).
- **[`logs/`](logs/)**: Log files from script execution.
- **[`scripts/`](scripts/)**: Shell scripts for pipeline automation.
  - **[`scripts/update.sh`](scripts/update.sh)**: Run the entire data processing pipeline.
  - **[`scripts/periodic_update.sh`](scripts/periodic_update.sh)**: Automate pipeline via cron.
  - **[`scripts/backup.sh`](scripts/backup.sh)**: Back up PostgreSQL volume and config files.
  - **[`scripts/restore.sh`](scripts/restore.sh)**: Restore PostgreSQL volume and config.
  - **[`scripts/initial_deployment.sh`](scripts/initial_deployment.sh)**: Set up a new server (Docker, dependencies).
  - **[`scripts/containers_deployment.sh`](scripts/containers_deployment.sh)**: Build and deploy Docker containers.
  - **[`scripts/delete_old_files.sh`](scripts/delete_old_files.sh)**: Delete old files from logs/ and backups/ directories.
  - **[`scripts/manage_checkpoint.sh`](scripts/manage_checkpoint.sh)**: Manage checkpoint files for processing.
  - **[`scripts/write_tchap_message.sh`](scripts/write_tchap_message.sh)**: Send notifications to Tchap (French government chat).
- **[`CROSSREFERENCE.md`](CROSSREFERENCE.md)**: Technical specification for JADE/BOFiP → LEGI cross-reference inference (RAG/graphRAG).

## ⚖️ License

This project is licensed under the [MIT License](./LICENSE).
