# MEDIATECH

[![License](https://img.shields.io/github/license/etalab-ia/mediatech?label=licence&color=red)](https://github.com/etalab-ia/mediatech/blob/main/LICENSE)
[![French version](https://img.shields.io/badge/🇫🇷-French%20version-blue)](./docs/README_fr.md)
[![Hugging Face collection](https://img.shields.io/badge/🤗-Hugging%20Face%20collection-yellow)](https://huggingface.co/collections/AgentPublic/mediatech-68309e15729011f49ef505e8)


## 📝 Description

This project processes public data made available by various administrations in order to facilitate access to vectorized and ready-to-use public data for AI applications in the public sector.
It includes scripts for downloading, processing, embedding, and inserting this data into a PostgreSQL database, and facilitates its export via various means.

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

#### PostgreSQL (PgVector) Database Configuration

1. Set up the environment variables in a [`.env`](.env) file based on the example in [`.env.example`](.env.example).

2. Export [`.env`](.env) variables :
   ```bash
   export $(grep -v '^#' .env | xargs)
   ```

3. Start the PostgreSQL container with Docker:
   ```bash
   docker compose up -d postgres
   ```

4. Check that the `pgvector_container` container is running:
   ```bash
   docker ps
   ```

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

- **[`main.py`](main.py)**: Main entry point to run the complete pipeline via CLI.
- **[`pyproject.toml`](pyproject.toml)**: Python project and dependency configuration.
- **[`Dockerfile`](Dockerfile)**: Defines the instructions to build the custom Docker image for Airflow, installing system dependencies, Python packages, and setting up the project environment.
- **[`docker-compose.yml`](docker-compose.yml)**: Orchestrates the multi-container setup, defining Airflow services and the PostgreSQL (PgVector) database.
- **[`.github/`](.github/)**: Contains GitHub Actions workflows for Continuous Integration and Continuous Deployment (CI/CD), automating testing and deployment processes.
- **[`download_and_processing/`](download_and_processing/)**: Contains scripts to download and extract files.
- **[`database/`](database/)**: Contains scripts to manage the database (table creation, data insertion).
- **[`docs/`](/docs/)**: Contains various documentation resources and tutorials.
  - **[`docs/hugging_face_rag_tutorial.ipynb`](/docs/hugging_face_rag_tutorial.ipynb)**: RAG Tutorial: How to load MediaTech's datasets from Hugging Face and use them in a RAG pipeline ?
  - **[`docs/reconstruct_vector_database.ipynb`](/docs/reconstruct_vector_database.ipynb)**: Tutorial: How to reconstruct a dataset without chunking and embedding from MediaTech parquet files uploaded to Hugging Face?
  - **[`docs/fr/`](/docs/fr/)**: Contains all documentation resources and tutorials translated into French.
- **[`utils/`](utils/)**: Contains utility functions shared across modules.
- **[`config/`](config/)**: Contains project configuration scripts.
- **[`logs/`](logs/)**: Contains log files to track [scripts](scripts/) execution.
- **[`scripts/`](scripts/)**: Contains all shell scripts, executed either automatically or manually in some cases.
  - **[`scripts/update.sh`](scripts/update.sh)**: Shell script to run the entire data processing pipeline.
  - **[`scripts/periodic_update.sh`](scripts/periodic_update.sh)**: Shell script to automate the pipeline on the virtual machine. This script is executed periodically by [`cron_config.txt`](cron_config.txt).
  - **[`scripts/backup.sh`](scripts/backup.sh)**: Shell script to back up the Pgvector (PostgreSQL) volume and some configuration files. This script is executed periodically by [`cron_config.txt`](cron_config.txt).
  - **[`scripts/restore.sh`](scripts/restore.sh)**: Shell script to restore the Pgvector (PostgreSQL) volume and configuration files if needed.
  - **[`scripts/initial_deployment.sh`](scripts/initial_deployment.sh)**: Sets up a new server environment by installing Docker, Docker Compose, and other system dependencies.
  - **[`scripts/containers_deployment.sh`](scripts/containers_deployment.sh)**:  Manages the application's lifecycle by building, initializing, and deploying the Docker containers as defined in [docker-compose.yml](docker-compose.yml). It must be executed after each update of the Mediatech CLI or other script not shared with the Airflow container, as defined in [docker-compose.yml](docker-compose.yml).
  - **[`scripts/check_running_dags.sh`](scripts/check_running_dags.sh)**: Checks the Airflow API to see if any data pipelines (DAGs) are currently running, used to safely lock the deployment process.
  - **[`scripts/delete_old_files.sh`](scripts/delete_old_files.sh)**: Shell script to automatically delete old files  from severals folders such as [logs/](logs/), [airflow_config/logs](airflow_config/logs) and [backups/](backups/). It keeps files from the last X days and deletes older ones. This script can be run manually or scheduled via cron to keep the folders clean.
  - **[`scripts/manage_checkpoint.sh`](scripts/manage_checkpoint.sh)** : Script shell permettant de gérer les différents fichiers de points de contrôle pour le traitement des fichiers. 
  - **[`scripts/write_tchap_message.sh`](scripts/write_tchap_message.sh)**: Sends a formatted message to a specified Tchap room. It takes the message content as an argument and uses environment variables for authentication and destination.
- **[`airflow_config`](airflow_config/)**: Contains all files related to Apache Airflow, including DAG definitions (`dags/`), configuration (`config/`), logs (`logs/`), and plugins (`plugins/`). This is where the data orchestration pipelines are defined and managed.

## ⚖️ License

This project is licensed under the [MIT License](./LICENSE).
