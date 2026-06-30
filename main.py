#!/usr/bin/env python3

"""Mediatech CLI.

Usage:
    main.py download_files (--all | --source=<source>) [--debug]
    main.py download_and_process_files (--all | --source=<source>) [--model=<model_name>] [--debug]
    main.py create_tables [--model=<model_name>] [--delete-existing] [--debug]
    main.py process_files (--all | --source=<source>) [--folder=<path>] [--model=<model_name>] [--debug]
    main.py split_table (--table=<name>) [--debug]
    main.py export_table (--table=<name>) [--output=<path>] [--split] [--debug]
    main.py upload_dataset (--all | --dataset-name=<name>) [--input=<path>] [--repository=<name>] [--private] [--debug]
    main.py infer_crossreferences (--source=<source>) [--model=<model_name>] [--debug]
    main.py clean_crossreferences (--source=<source>) [--reset-catalog] [--yes] [--debug]
    main.py add_fts_columns [--debug]
    main.py -h | --help

Commands:
    download_files              Download files from sources
    download_and_process_files  Download and process files from sources
    create_tables               Create database tables (with option to delete existing ones)
    process_files               Process data from specific source or all sources and insert into database
    split_table                 Split a table into multiple smaller tables based on source and criteria
    export_table                Export table to Parquet files
    upload_dataset              Upload dataset to Hugging Face
    infer_crossreferences       Infer JADE/BOFIP -> LEGI cross-references
    clean_crossreferences       Clean cross-reference tables (mentions, edges, state) for reprocessing
    add_fts_columns             Add full-text search columns (tsvector + GIN index) for hybrid search

Options:
    --delete-existing       Delete existing tables before creating new ones
    --all                   Select all data sources from the data configuration file
    --model=<model_name>    Embedding model name [default: BAAI/bge-m3]. It is mandatory to specify the same model for all commands.
    --source=<source>       Source to process (legi, jade, bofip, all)
    --table=<name>          Table name to export or split (legi, jade, bofip)
    --folder=<path>         Folder containing unprocessed data
    --input=<path>          Input path of the dataset to upload
    --dataset-name=<name>   Name of the dataset to upload to Hugging Face
    --repository=<name>     Hugging Face repository name [default: AgentPublic]
    --output=<path>         Output folder for Parquet files
    --split                 Split the table into smaller tables before exporting
    --private               Upload dataset as private on Hugging Face
    --reset-catalog         Also TRUNCATE legi_reference_catalog (only valid with --source=all)
    --yes                   Skip the interactive confirmation prompt for destructive operations
    --debug                 Enable debug logging
    -h --help               Show this help message

Examples:
    main.py create_tables --model BAAI/bge-m3 --delete-existing
    main.py download_files --all
    main.py download_and_process_files --source lemi --model BAAI/bge-m3 --debug
    main.py download_and_process_files --all --model BAAI/bge-m3
    main.py process_files --source jade --model BAAI/bge-m3
    main.py process_files --all --folder data/unprocessed --model BAAI/bge-m3
    main.py split_table --table legi
    main.py export_table --table legi --split
    main.py export_table --table all --output data/parquet
    main.py upload_dataset --input data/parquet/bofip.parquet --dataset-name bofip --repository AgentPublic --private
    main.py upload_dataset --all --repository AgentPublic
    main.py infer_crossreferences --source all --model BAAI/bge-m3
    main.py infer_crossreferences --source jade
    main.py clean_crossreferences --source jade
    main.py clean_crossreferences --source all
    main.py add_fts_columns
"""

import os
import re
import sys

from docopt import docopt

from config import (
    BASE_PATH,
    EMBEDDING_MODEL,
    HF_TOKEN,
    SOURCE_MAP,
    config_file_path,
    data_history_path,
    get_logger,
    parquet_files_folder,
    setup_logging,
)
from database import create_all_tables, export_table_to_parquet, split_legi_table
from download_and_processing import (
    download_and_optionally_process_all_files,
    download_and_optionally_process_files,
    process_all_data,
    process_data,
)
from utils import format_model_name


def main():
    try:
        args = docopt(__doc__)

        # Setup logging
        debug_mode = args.get("--debug", False)
        setup_logging(debug=debug_mode)
        logger = get_logger(__name__)

        # Download files
        if args["download_files"]:
            if args["--all"]:
                logger.info(
                    f"Downloading all files using config: {config_file_path} and history: {data_history_path}"
                )
                download_and_optionally_process_all_files(
                    process=False,
                    model=args["--model"] if args["--model"] else EMBEDDING_MODEL,
                )
            else:
                source = args["--source"]

                if source in SOURCE_MAP:
                    logger.info(
                        f"Downloading and processing {source} files using config: {config_file_path} and history: {data_history_path}"
                    )

                    download_and_optionally_process_files(
                        table_name=source,
                        process=False,
                        model=args["--model"] if args["--model"] else EMBEDDING_MODEL,
                    )
                else:
                    logger.error(f"Unknown source: {source}")
                    return 1

        # Download and process files
        # This method as a better storage optimization compared to download_files + process_files)
        elif args["download_and_process_files"]:
            if args["--all"]:
                logger.info(
                    f"Downloading and processing all files using config: {config_file_path} and history: {data_history_path}"
                )
                download_and_optionally_process_all_files(
                    process=True,
                    model=args["--model"] if args["--model"] else EMBEDDING_MODEL,
                )
            else:
                source = args["--source"]

                if source in SOURCE_MAP:
                    logger.info(
                        f"Downloading and processing {source} files using config: {config_file_path} and history: {data_history_path}"
                    )
                    download_and_optionally_process_files(
                        table_name=source,
                        process=True,
                        model=args["--model"] if args["--model"] else EMBEDDING_MODEL,
                    )
                else:
                    logger.error(f"Unknown source: {source}")
                    return 1

        # Create tables
        elif args["create_tables"]:
            delete_existing = True if args["--delete-existing"] else False
            model = args["--model"] if args["--model"] else EMBEDDING_MODEL
            logger.info(
                f"Creating tables with model {model} (delete_existing={delete_existing})"
            )
            create_all_tables(delete_existing=delete_existing, model=model)

        # Process data
        elif args["process_files"]:
            model = args["--model"] if args["--model"] else EMBEDDING_MODEL
            if args["--all"]:
                folder = args["--folder"] or os.path.join(BASE_PATH, "data/unprocessed")
                logger.info(f"Processing all unprocessed data from folder: {folder}, with model {model} (EMBEDDING_MODEL={EMBEDDING_MODEL})")
                process_all_data(model=model)
            else:
                source = args["--source"]

                if source in SOURCE_MAP:
                    logger.info(f"Processing data from source: {source}")
                    process_data(table_name=source, model=model, streaming=True)
                else:
                    logger.error(f"Unknown source: {source}")
                    return 1

        # Split table into smaller tables based on several criteria
        elif args["split_table"]:
            table = args["--table"] if args["--table"] else "unknown"
            if table == "legi":
                logger.info(f"Splitting {table.upper()} table into smaller tables")
                split_legi_table(source_table=table, export_to_parquet=False)
            else:
                logger.error(f"Splitting is not implemented for the {table} table.")
                return 1

        # Export tables to parquet
        elif args["export_table"]:
            output = args["--output"] or parquet_files_folder
            table = args["--table"] if args["--table"] else None
            if table is not None:
                logger.info(
                    f"Exporting {table} PgVector tables to Parquet in folder: {output}"
                )
                if args["--split"]:
                    if table == "legi":
                        split_legi_table(source_table=table, export_to_parquet=True)
                else:
                    export_table_to_parquet(table_name=table, parquet_folder=output)

        # Upload dataset to Hugging Face
        elif args["upload_dataset"]:
            from utils.hugging_face import HuggingFace, upload_dataset_task

            if args["--all"]:
                logger.info("Uploading all datasets to Hugging Face")
                private = True if args["--private"] else False
                repository = (
                    args["--repository"] if args["--repository"] else "AgentPublic"
                )
                hf = HuggingFace(hugging_face_repo=repository, token=HF_TOKEN)
                hf.upload_all_datasets(
                    config_file_path=config_file_path, private=private
                )
            else:
                dataset_name = args[
                    "--dataset-name"
                ]  # The name of the dataset to upload (e.g., service-public, travail-emploi, etc.)
                input_path = (
                    args["--input"]
                    if args["--input"]
                    else os.path.join(
                        parquet_files_folder,
                        f"{dataset_name.lower().replace('-', '_')}",
                    )  # Default folder path for the dataset (e.g., ./data/parquet/bofip)
                )
                repository = (
                    args["--repository"] if args["--repository"] else "AgentPublic"
                )
                private = True if args["--private"] else False

                logger.info(
                    f"Uploading dataset {dataset_name} from {input_path} to Hugging Face (private={private})"
                )
                upload_dataset_task(
                    dataset_name=dataset_name,
                    token=HF_TOKEN,
                    repository=repository,
                    private=private,
                    local_folder_path=input_path,
                )

        # Infer cross-references
        elif args["infer_crossreferences"]:
            from crossreference import infer_crossreferences

            source = args["--source"]
            if source not in ("jade", "bofip", "all"):
                logger.error(f"Invalid source for infer_crossreferences: {source}. Use jade, bofip, or all.")
                return 1

            model = args["--model"] if args["--model"] else EMBEDDING_MODEL
            model_suffix = format_model_name(model)
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", model_suffix):
                logger.error(
                    "Invalid model name for infer_crossreferences: %s (formatted=%s). "
                    "Allowed characters after provider prefix: a-z, A-Z, 0-9, underscore, hyphen.",
                    model,
                    model_suffix,
                )
                return 1
            logger.info(
                f"Inferring cross-references (source={source}, model={model})"
            )
            summary = infer_crossreferences(source=source, model=model, debug=debug_mode)
            if summary.get("failed_docs", 0) > 0:
                logger.error(
                    "infer_crossreferences finished with %s failed document(s)",
                    summary.get("failed_docs"),
                )
                return 1

        # Add full-text search columns for hybrid search
        elif args["add_fts_columns"]:
            from database import get_connection

            logger.info("Adding FTS columns (tsvector + GIN index) to legi, jade, bofip tables...")
            conn = get_connection()
            try:
                sql_path = os.path.join(BASE_PATH, "database", "sql_scripts", "add_fts_columns.sql")
                with open(sql_path) as f:
                    sql = f.read()
                cursor = conn.cursor()
                cursor.execute(sql)
                conn.commit()
                logger.info("FTS columns added successfully. Hybrid search is now enabled.")
            finally:
                conn.close()

        # Clean cross-reference data
        elif args["clean_crossreferences"]:
            from database.cross_reference_manage import clean_cross_reference_data

            source = args["--source"]
            reset_catalog = bool(args.get("--reset-catalog"))
            assume_yes = bool(args.get("--yes"))
            if source not in ("jade", "bofip", "all"):
                logger.error(f"Invalid source for clean_crossreferences: {source}. Use jade, bofip, or all.")
                return 1
            if reset_catalog and source != "all":
                logger.error(
                    "--reset-catalog is only valid with --source=all"
                )
                return 1

            scope_label = "ALL JADE+BOFIP" if source == "all" else source.upper()
            extras = " AND legi_reference_catalog" if reset_catalog else ""
            warning = (
                f"clean_crossreferences will DELETE mentions, edges, and source-state for "
                f"{scope_label}{extras}. This is irreversible."
            )
            logger.warning(warning)
            if not assume_yes:
                if not sys.stdin.isatty():
                    logger.error(
                        "Refusing to proceed without --yes when stdin is not a TTY. "
                        "Re-run with --yes to confirm."
                    )
                    return 1
                try:
                    response = input("Type 'yes' to confirm: ").strip().lower()
                except EOFError:
                    response = ""
                if response != "yes":
                    logger.info("Aborted by user.")
                    return 1

            logger.info(
                f"Cleaning cross-reference data for source={source} "
                f"(reset_catalog={reset_catalog})"
            )
            clean_cross_reference_data(source=source, reset_catalog=reset_catalog)
            logger.info(f"Successfully cleaned cross-reference data for {source}")

        return 0

    except Exception as e:
        logger.error(f"An error occurred: {e}")
        return 1


if __name__ == "__main__":
    exit_code = main()
    sys.exit(exit_code)
