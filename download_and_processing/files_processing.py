import gc
import json
import os
import subprocess
import sys
import tarfile
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor
from contextlib import nullcontext
from datetime import datetime

import pandas as pd
import xxhash
from bs4 import BeautifulSoup
from openai import PermissionDeniedError

from config import (
    BASE_PATH,
    BATCH_SIZE_DOCS,
    EMBEDDING_BATCH_MAX_SIZE,
    EMBEDDING_RETRY_ATTEMPTS,
    EMBEDDING_MODEL,
    ENABLE_BATCH_EMBEDDING,
    ENABLE_PARALLEL_PROCESSING,
    MAX_WORKERS,
    SOURCE_MAP,
    config_file_path,
    get_logger,
)
from database import insert_data, refresh_table, remove_data, upsert_bofip_node, upsert_jade_node, upsert_legi_node
from utils import (
    CheckpointManager,
    _make_schedule,
    embed_texts_with_retry,
    format_subtitles,
    generate_embeddings_with_retry,
    load_config,
    make_chunks,
    PerfTelemetry,
    remove_file,
    remove_folder,
)

logger = get_logger(__name__)

# Setting a higher recursion limit for processing large files
sys.setrecursionlimit(10000)

_SMART_PROCESS_TARGETS = {"legi", "jade", "bofip"}
_SMART_PROCESS_HAS_RUN = True # False


def _telemetry_stage(telemetry: PerfTelemetry | None, stage_name: str):
    if telemetry is None:
        return nullcontext()
    return telemetry.stage(stage_name)


def _embed_texts(
    chunk_texts: list[str],
    model: str,
    telemetry: PerfTelemetry | None = None,
) -> list[list[float]]:
    if not chunk_texts:
        return []
    if ENABLE_BATCH_EMBEDDING:
        return embed_texts_with_retry(
            texts=chunk_texts,
            model=model,
            attempts=EMBEDDING_RETRY_ATTEMPTS,
            max_batch_size=EMBEDDING_BATCH_MAX_SIZE,
            split_on_failure=True,
            retry_observer=(telemetry.add_retry if telemetry else None),
        )
    embeddings = []
    for chunk_text in chunk_texts:
        embeddings.append(
            generate_embeddings_with_retry(
                data=chunk_text,
                attempts=EMBEDDING_RETRY_ATTEMPTS,
                model=model,
            )[0]
        )
    return embeddings


def _persist_dila_payload(payload: dict):
    table_name = payload.get("table_name")
    data_to_insert = payload.get("data_to_insert", [])
    graph_type = payload.get("graph_type")
    if not data_to_insert:
        return
    insert_data(data=data_to_insert, table_name=table_name)
    if graph_type == "legi":
        upsert_legi_node(data_to_insert)
    elif graph_type == "jade":
        upsert_jade_node(data_to_insert)


def _prepare_dila_payload_from_file(file_path: str, model: str) -> dict | None:
    worker_telemetry = PerfTelemetry(run_name="worker_dila", enabled=True)
    file_name = os.path.basename(file_path)
    with worker_telemetry.stage("parse"):
        tree = ET.parse(file_path)
        root = tree.getroot()
    payload = _process_dila_xml_content(
        root=root,
        file_name=file_name,
        model=model,
        persist=False,
        telemetry=worker_telemetry,
    )
    if payload is None:
        return None
    payload["file_name"] = file_name
    payload["worker_stage_seconds"] = worker_telemetry.stage_seconds
    return payload


def _prepare_bofip_payload_from_paths(
    xml_path: str,
    html_path: str,
    rel_xml_path: str,
    model: str,
) -> list:
    worker_telemetry = PerfTelemetry(run_name="worker_bofip", enabled=True)
    with worker_telemetry.stage("parse"):
        with open(xml_path, "rb") as xf, open(html_path, "rb") as hf:
            xml_content = xf.read()
            html_content = hf.read()

    data_to_insert = _process_bofip_document(
        xml_content=xml_content,
        html_content=html_content,
        file_path=rel_xml_path,
        model=model,
        telemetry=worker_telemetry,
    )

    return {
        "data_to_insert": data_to_insert,
        "worker_stage_seconds": worker_telemetry.stage_seconds,
    }


def _merge_worker_stage_seconds(
    telemetry: PerfTelemetry | None,
    worker_stage_seconds: dict | None,
):
    if telemetry is None or not worker_stage_seconds:
        return
    for stage_name, seconds in worker_stage_seconds.items():
        telemetry.add_stage_time(stage_name, float(seconds))


def _extract_bofip_result_payload(result_obj) -> tuple[list, dict]:
    if isinstance(result_obj, dict):
        return result_obj.get("data_to_insert", []), result_obj.get("worker_stage_seconds", {})
    return result_obj or [], {}


def _extract_dila_result_payload(result_obj) -> tuple[dict | None, dict]:
    if isinstance(result_obj, dict):
        return result_obj, result_obj.get("worker_stage_seconds", {})
    return result_obj, {}


def _get_selected_folder(table_name: str) -> str:
    """Return the selected-data folder for a table name."""
    return os.path.join(BASE_PATH, "data", "selected", table_name.lower())


def _directory_has_suffix(path: str, suffix: str) -> bool:
    """Check if a directory contains at least one file ending with suffix."""
    if not os.path.isdir(path):
        return False
    for _, _, files in os.walk(path):
        if any(name.endswith(suffix) for name in files):
            return True
    return False


def _has_bofip_selected_documents(selected_folder: str) -> bool:
    """Check if selected BOFiP folder has at least one document.xml + data.html pair."""
    if not os.path.isdir(selected_folder):
        return False

    for _, _, files in os.walk(selected_folder):
        file_set = set(files)
        if "document.xml" in file_set and "data.html" in file_set:
            return True
    return False


def _run_smart_process_tax_if_needed(table_name: str):
    """Run smart pre-processing once before handling LEGI/JADE/BOFiP sources."""
    global _SMART_PROCESS_HAS_RUN

    if table_name.lower() not in _SMART_PROCESS_TARGETS:
        return
    if _SMART_PROCESS_HAS_RUN:
        return

    script_path = os.path.join(BASE_PATH, "download_and_processing", "smart_process_tax.sh")
    if not os.path.isfile(script_path):
        raise FileNotFoundError(
            f"Smart pre-processing script not found at {script_path}"
        )

    logger.info(
        "Running smart pre-processing before LEGI/JADE/BOFiP content processing"
    )
    try:
        result = subprocess.run(
            ["bash", script_path],
            cwd=BASE_PATH,
            check=True,
            capture_output=True,
            text=True,
        )
        if result.stdout:
            logger.debug(result.stdout)
        if result.stderr:
            logger.debug(result.stderr)
        logger.info("Smart pre-processing finished successfully")
    except subprocess.CalledProcessError as e:
        logger.error(
            f"Smart pre-processing failed with return code {e.returncode}: {e.stderr}"
        )
        raise

    _SMART_PROCESS_HAS_RUN = True
#TODO 1: we need a second pass to get other links such as laws 
# that modify the current code. 
# We can use the "LIENS" tag in the XML files to get all the relationships 
# between documents and then update incrementally the database with these links. 
# This way we will be able to have a more complete graph of legal documents. 
# We can also use this information to create embeddings that take into account 
# the relationships between documents, which can improve the search results.
# TODO 2: After that we will also parse the content of the documents 
# to infer references to other documents that are not explicitly mentioned in the "LIENS" tag, 
# but that are present in the text.



def _process_dila_xml_content(
    root: ET.Element,
    file_name: str,
    model: str,
    persist: bool = True,
    telemetry: PerfTelemetry | None = None,
):
    """Processes a single DILA XML file, prepares, and inserts its content into the database.

    This function acts as a dispatcher based on the filename prefix. It handles
    different XML structures for tax legal document types (`LEGIARTI`, `JADETEXT`).

    For each document, it extracts metadata and textual content, splits the text
    into manageable chunks, generates a vector embedding for each chunk, and
    then performs a batch insertion of the processed data into the corresponding
    database table.

    Args:
        root (ET.Element): The root element of the parsed XML file.
        file_name (str): The name of the XML file, used to determine the
                         processing logic.
        model (str): The identifier for the embedding model to be used.

    Raises:
        PermissionDeniedError: If the embedding generation fails due to an
                               API key issue.
        Exception: For any other errors encountered during file processing,
                   which are logged and re-raised.
    """
   
    if file_name.startswith("LEGIARTI") and file_name.endswith(".xml"):
        table_name = "legi"
        
        try:
            category = root.find(".//CONTEXTE//TEXTE").get("cid", None)
            
            ministry = root.find(".//CONTEXTE//TEXTE").get("ministere", None)
            status = root.find(".//ETAT").text
            cid = root.find(".//ID").text  # doc_id
            nature = root.find(".//NATURE").text
            title = (
                root.find(".//CONTEXTE//TEXTE//TITRE_TXT")
                .get("c_titre_court")
                .strip(".")
            )
          
            subtitles = []
            for elem in root.find(".//CONTEXTE//TEXTE").iter("TITRE_TM"):
                subtitles.append(elem.text)
            subtitles = " - ".join(subtitles)
            if not subtitles:
                subtitles = None
            number = root.find(".//NUM").text

            start_date = datetime.strptime(
                root.find(".//DATE_DEBUT").text, "%Y-%m-%d"
            ).strftime("%Y-%m-%d")
            end_date = datetime.strptime(
                root.find(".//DATE_FIN").text, "%Y-%m-%d"
            ).strftime("%Y-%m-%d")
            full_title = root.find(".//TITRE_TXT").text

            nota = []
            contenu_nota = root.find(".//NOTA//CONTENU")
            for paragraph in contenu_nota.findall(".//p"):
                nota.append(paragraph.text)
            nota = "\n".join(nota).strip()
            if not nota:
                nota = None

            links = []
            for link in root.find(".//LIENS"):
                links.append(
                    {
                        "text_doc_id": link.get("cidtexte"),
                        "text_signature_date": link.get("datesignatexte"),
                        "doc_id": link.get("id"),
                        "category": link.get("naturetexte"),
                        "nor": link.get("nortexte"),
                        "number": link.get("num"),
                        "text_number": link.get("numtexte"),
                        "link_direction": link.get("sens"),
                        "link_type": link.get("typelien"),
                        "title": link.text,
                    }
                )

            contenu = root.find(".//BLOC_TEXTUEL/CONTENU")
            text_content = []

            if contenu is not None:
                # Extract all text
                content = ET.tostring(contenu, encoding="unicode", method="xml")
                content = "".join(ET.fromstring(content).itertext())
                # Post-process the text to improve readability
                lines = content.splitlines()  # Split the text into lines
                cleaned_lines = [
                    line for line in lines if line
                ]  # Remove empty lines and extra spaces
                content = "\n".join(
                    cleaned_lines
                )  # Rejoin the cleaned lines with a newline
                text_content.append(content)
            text_content = "\n".join(text_content)

            data_to_insert = []
            with _telemetry_stage(telemetry, "chunking"):
                chunks = make_chunks(text=text_content)
            chunk_rows = []
            chunk_texts = []
            for k, text in enumerate(chunks):
                try:
                    chunk_index =  k + 1 # Start chunk numbering from 1
                    chunk_text = f"{full_title}"
                    if number:
                        chunk_text += f" - Article {number}"
                    # Adding subtitles only if the text is long enough
                    if subtitles and len(text) > 200:
                        context = format_subtitles(subtitles=subtitles)
                        if context and len(context) < len(text):
                            chunk_text += f"\n{context}"  # Augment the chunk text with subtitles concepts
                    chunk_text += f"\n{text}"

                    chunk_xxh64 = xxhash.xxh64(
                        chunk_text.encode("utf-8"), seed=2025
                    ).hexdigest()

                    chunk_id = f"{cid}_{chunk_index}"  # Unique ID for the chunk
                    chunk_rows.append(
                        (
                            chunk_id,
                            cid,
                            chunk_index,
                            chunk_xxh64,
                            nature,
                            category,
                            ministry,
                            status,
                            title,
                            full_title,
                            subtitles,
                            number,
                            start_date,
                            end_date,
                            nota,
                            json.dumps(links),
                            text,
                            chunk_text,
                        )
                    )
                    chunk_texts.append(chunk_text)
                except PermissionDeniedError as e:
                    logger.error(
                        f"PermissionDeniedError (API key issue) for file {file_name}: {e}"
                    )
                    raise e

            with _telemetry_stage(telemetry, "embedding"):
                embeddings_list = _embed_texts(chunk_texts=chunk_texts, model=model, telemetry=telemetry)
            for row_data, embeddings in zip(chunk_rows, embeddings_list):
                data_to_insert.append((*row_data, embeddings))

            if telemetry is not None:
                telemetry.add_counter("docs_processed", 1)
                telemetry.add_counter("chunks_produced", len(data_to_insert))

            # Inserting all chunks at once
            if data_to_insert:
                if persist:
                    with _telemetry_stage(telemetry, "postgres_insert"):
                        insert_data(data=data_to_insert, table_name=table_name)
                    with _telemetry_stage(telemetry, "graph_upsert"):
                        upsert_legi_node(data_to_insert)
                    if telemetry is not None:
                        telemetry.add_counter("rows_written", len(data_to_insert))
                return {
                    "table_name": table_name,
                    "graph_type": "legi",
                    "data_to_insert": data_to_insert,
                }

        except Exception as e:
            logger.error(f"Error processing file {file_name}: {e}")
            raise e

    
    elif file_name.startswith("CETATEXT") and file_name.endswith(".xml"):
        
        table_name = "jade"
        try:
            cid = root.find(".//ID").text
            nature = root.find(".//NATURE").text if root.find(".//NATURE") is not None else None
            title_elem = root.find(".//TITRE")
            title = title_elem.text if title_elem is not None else None
            number_elem = root.find(".//NUMERO")
            number = number_elem.text if number_elem is not None else None
            solution_elem = root.find(".//SOLUTION")
            solution = solution_elem.text if solution_elem is not None else None
            jurisdiction_elem = root.find(".//JURIDICTION")
            jurisdiction = jurisdiction_elem.text if jurisdiction_elem is not None else None
            formation_elem = root.find(".//FORMATION")
            formation = formation_elem.text if formation_elem is not None else None

            date_elem = root.find(".//DATE_DEC")
            try:
                decision_date = datetime.strptime(
                    date_elem.text, "%Y-%m-%d"
                ).strftime("%Y-%m-%d") if date_elem is not None and date_elem.text else None
            except ValueError:
                decision_date = date_elem.text if date_elem is not None else None

            text_content = []

            # Use ana element if it exists and keep the id for more details, otherwise fallback to BLOC_TEXTUEL/CONTENU
            ana_elem = root.find(".//ANA")
            if ana_elem is not None:
                text_content.append(ana_elem.text)
            else:
                logger.warning(f"ANA element not found in file {file_name}. Text content may be incomplete.")
                contenu = root.find(".//BLOC_TEXTUEL//CONTENU")
                if contenu is not None:
                    content = ET.tostring(contenu, encoding="unicode", method="xml")
                    content = "".join(ET.fromstring(content).itertext())
                    lines = content.splitlines()
                    cleaned_lines = [line for line in lines if line]
                    content = "\n".join(cleaned_lines)
                    text_content.append(content)
            
            text_content = "\n".join(text_content)
            data_to_insert = []
            with _telemetry_stage(telemetry, "chunking"):
                chunks = make_chunks(text=text_content)
            chunk_rows = []
            chunk_texts = []
            for k, text in enumerate(chunks):
                try:
                    chunk_index = k + 1
                    chunk_text = f"{title}\n{text}" if title else text

                    chunk_xxh64 = xxhash.xxh64(
                        chunk_text.encode("utf-8"), seed=2025
                    ).hexdigest()

                    chunk_id = f"{cid}_{chunk_index}"
                    chunk_rows.append(
                        (
                            chunk_id,
                            cid,
                            chunk_index,
                            chunk_xxh64,
                            nature,
                            solution,
                            title,
                            number,
                            decision_date,
                            jurisdiction,
                            formation,
                            text_content,
                            chunk_text,
                        )
                    )
                    chunk_texts.append(chunk_text)
                except PermissionDeniedError as e:
                    logger.error(
                        f"PermissionDeniedError (API key issue) for file {file_name}: {e}"
                    )
                    raise e

            with _telemetry_stage(telemetry, "embedding"):
                embeddings_list = _embed_texts(chunk_texts=chunk_texts, model=model, telemetry=telemetry)
            for row_data, embeddings in zip(chunk_rows, embeddings_list):
                data_to_insert.append((*row_data, embeddings))

            if telemetry is not None:
                telemetry.add_counter("docs_processed", 1)
                telemetry.add_counter("chunks_produced", len(data_to_insert))

            if data_to_insert:
                if persist:
                    with _telemetry_stage(telemetry, "postgres_insert"):
                        insert_data(data=data_to_insert, table_name=table_name)
                    with _telemetry_stage(telemetry, "graph_upsert"):
                        upsert_jade_node(data_to_insert)
                    if telemetry is not None:
                        telemetry.add_counter("rows_written", len(data_to_insert))
                return {
                    "table_name": table_name,
                    "graph_type": "jade",
                    "data_to_insert": data_to_insert,
                }

        except Exception as e:
            logger.error(f"Error processing file {file_name}: {e}")
            raise e

    return None


def _handle_dila_suppression_list(lines: list[str], table_name: str, source_name: str):
    """
    Processes a suppression list from DILA's files, to remove documents from a database table.
    For each line provided, it extracts a document ID from the end of the path-like
    string and calls a function to remove the corresponding data from the specified
    table.

    Args:
        lines (list[str]): A list of strings from the suppression file.
        table_name (str): The name of the database table to modify.
        source_name (str): The name of the source file for logging purposes.
    """
    try:
        doc_ids_to_remove = [
            line.strip().split("/")[-1] for line in lines if line.strip()
        ]
        if doc_ids_to_remove:
            logger.debug(
                f"Removing {len(doc_ids_to_remove)} document IDs from the '{table_name.upper()}' table based on suppression list in {source_name}"
            )
            for doc_id in doc_ids_to_remove:
                remove_data(table_name=table_name, column="doc_id", value=doc_id)
    except Exception as e:
        logger.error(
            f"Error removing document IDs from suppression list for {source_name}: {e}"
        )
        raise Exception(
            f"Error removing document IDs from suppression list for {source_name}: {e}"
        )


def process_dila_xml_files(
    source_path: str,
    streaming: bool = True,
    model: str = EMBEDDING_MODEL,
    telemetry: PerfTelemetry | None = None,
):
    """Processes DILA XML files from a directory or a compressed archive.

    This function operates in two modes based on the `streaming` flag.
    - If `streaming` is True (default), it treats `source_path` as a .tar.gz
      archive, processing XML files in memory without extraction. The archive
      is deleted after processing is complete.
    - If `streaming` is False, it treats `source_path` as a directory,
      iterating through XML files on disk. Each file is deleted after being
      processed.

    The function implements a checkpoint system to resume processing from the last
    successfully processed file in case of errors.

    Args:
        source_path (str): The path to the source data. This is a path to a
            `.tar.gz` archive if streaming, or a directory if not.
        streaming (bool, optional): Determines the processing mode.
            Defaults to True.
        model (str, optional): The identifier for the embedding model to be
            used in the underlying processing. Defaults to EMBEDDING_MODEL.
    """
    checkpoint = CheckpointManager(source_path)

    if streaming:
        logger.info(f"Processing files directly from archive: {source_path}")

        # Load checkpoint to resume from last position
        last_processed_index = checkpoint.load()

        try:
            with tarfile.open(source_path, "r:gz") as tar:
                files = [
                    m
                    for m in tar.getmembers()
                    if m.isfile() and m.name.endswith(".xml")
                ]

                total_files = len(files)
                logger.info(
                    f"Total files to process: {total_files}, Starting from index: {last_processed_index + 1}"
                )

                batch_size = 50
                for i in range(0, len(files), batch_size):
                    batch_files = files[i : i + batch_size]
                    batch_num = i // batch_size + 1
                    total_batches = (len(files) - 1) // batch_size + 1
                    if batch_num % 10 == 0 or batch_num == total_batches:
                        logger.info(
                            f"Processing batch {batch_num}/{total_batches} of {os.path.basename(source_path)} ({len(batch_files)} files)"
                        )

                    for idx, file in enumerate(batch_files):
                        file_global_index = i + idx

                        # Skip already processed files
                        if file_global_index <= last_processed_index:
                            logger.debug(
                                f"Skipping already processed file index {file_global_index}"
                            )
                            continue

                        file_object = None
                        file_content = None
                        root = None

                        try:
                            file_name = os.path.basename(file.name)
                            # Reading file in memory
                            file_object = tar.extractfile(file)
                            if file_object:
                                with file_object as f:
                                    file_content = f.read()
                            with _telemetry_stage(telemetry, "parse"):
                                root = ET.fromstring(file_content)
                            _process_dila_xml_content(
                                root=root,
                                file_name=file_name,
                                model=model,
                                persist=True,
                                telemetry=telemetry,
                            )

                            # Save checkpoint after successful processing
                            checkpoint.save(
                                file_global_index,
                                metadata={"file_name": file_name, "type": "dila_xml"},
                            )

                        except ET.ParseError as e:
                            logger.error(
                                f"XML parsing error for file {file_name} (index {file_global_index}): {e}"
                            )
                            # Continue to next file for parse errors
                            checkpoint.save(
                                file_global_index,
                                metadata={
                                    "file_name": file_name,
                                    "error": "parse_error",
                                },
                            )
                            continue
                        except Exception as e:
                            logger.error(
                                f"Error processing file {file.name} in archive (index {file_global_index}): {e}"
                            )
                            logger.error(
                                "Progress saved. Restart the process to resume from this point."
                            )
                            raise e

                    gc.collect()

                # All files processed successfully, remove checkpoint
                checkpoint.remove()
                logger.info(
                    f"Successfully processed all {total_files} files from {source_path}"
                )

        except Exception as e:
            logger.error(f"Error processing archive {source_path}: {e}")
            raise e
        finally:
            # Only remove archive if all files were processed successfully
            if not checkpoint.exists():
                remove_file(file_path=source_path)
                logger.info(f"Archive removed: {source_path}")
            else:
                logger.info(f"Archive kept for resume: {source_path}")
            gc.collect()
    else:
        logger.info(f"Processing files from directory: {source_path}")

        # Load checkpoint for non-streaming mode
        last_processed_index = checkpoint.load()
        processed_count = 0

        all_file_paths = []
        for root_dir, dirs, files in os.walk(source_path):
            xml_files = [f for f in files if f.endswith(".xml")]
            for file_name in xml_files:
                all_file_paths.append(os.path.join(root_dir, file_name))

        all_file_paths = sorted(all_file_paths)

        if ENABLE_PARALLEL_PROCESSING and MAX_WORKERS > 1:
            for i in range(0, len(all_file_paths), max(1, BATCH_SIZE_DOCS)):
                batch_paths = all_file_paths[i : i + max(1, BATCH_SIZE_DOCS)]
                indexed_batch = [
                    (global_idx, file_path)
                    for global_idx, file_path in enumerate(batch_paths, start=i)
                    if global_idx > last_processed_index
                ]
                if not indexed_batch:
                    continue

                with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
                    future_by_path = {
                        file_path: executor.submit(_prepare_dila_payload_from_file, file_path, model)
                        for _, file_path in indexed_batch
                    }

                    for file_global_index, file_path in indexed_batch:
                        future = future_by_path[file_path]
                        file_name = os.path.basename(file_path)
                        try:
                            payload_obj = future.result()
                            payload, worker_stage_seconds = _extract_dila_result_payload(payload_obj)
                            _merge_worker_stage_seconds(telemetry, worker_stage_seconds)

                            if payload:
                                if telemetry is not None:
                                    telemetry.add_counter("docs_processed", 1)
                                    telemetry.add_counter(
                                        "chunks_produced",
                                        len(payload.get("data_to_insert", [])),
                                    )
                                with _telemetry_stage(telemetry, "postgres_insert"):
                                    _persist_dila_payload(payload)
                                if telemetry is not None:
                                    telemetry.add_counter(
                                        "rows_written",
                                        len(payload.get("data_to_insert", [])),
                                    )

                            checkpoint.save(
                                file_global_index,
                                metadata={"file_name": file_name, "type": "dila_xml"},
                            )
                        except Exception as e:
                            logger.error(
                                f"Error processing file {file_name} (index {file_global_index}): {e}"
                            )
                            logger.error(
                                "Progress saved. Restart the process to resume from this point."
                            )
                            raise e
                        finally:
                            remove_file(file_path=file_path)
                    gc.collect()
        else:
            for file_path in all_file_paths:
                file_name = os.path.basename(file_path)
                # Skip already processed files
                if processed_count <= last_processed_index:
                    processed_count += 1
                    continue

                try:
                    with _telemetry_stage(telemetry, "parse"):
                        tree = ET.parse(file_path)
                        root = tree.getroot()
                    _process_dila_xml_content(
                        root=root,
                        file_name=file_name,
                        model=model,
                        persist=True,
                        telemetry=telemetry,
                    )

                    checkpoint.save(
                        processed_count,
                        metadata={"file_name": file_name, "type": "dila_xml"},
                    )
                    processed_count += 1

                except Exception as e:
                    logger.error(
                        f"Error processing file {file_name} (index {processed_count}): {e}"
                    )
                    logger.error(
                        "Progress saved. Restart the process to resume from this point."
                    )
                    raise e
                finally:
                    remove_file(file_path=file_path)
                    gc.collect()

        # All files processed successfully, remove checkpoint
        checkpoint.remove()
        logger.info(f"Successfully processed all files from {source_path}")



def _parse_bofip_path(file_path: str) -> dict:
    """
    Parse the path of a file inside a BOFiP archive to extract taxonomy information.

    BOFiP archives follow this directory hierarchy::

        BOFiP/documents/Contenu/{document_type}/[{domain}/]{document_number}/{version_date}/

    The version-date directory is always the deepest directory and its siblings are
    ``document.xml`` and ``data.html``.

    Args:
        file_path (str): Path of the file inside the archive (e.g.,
            ``"BOFiP/documents/Contenu/Formulaire/BA/14604-PGP/2025-07-23/document.xml"``).

    Returns:
        dict: Dictionary with keys: ``category_path``, ``document_type``, ``domain``,
            ``document_number``, ``version_date``.
    """
    parts = file_path.replace("\\", "/").split("/")
    # Strip the filename (document.xml or data.html)
    dir_parts = parts[:-1]

    # Locate "Contenu" in the path
    contenu_idx = next(
        (i for i, p in enumerate(dir_parts) if p == "Contenu"), None
    )

    if contenu_idx is None:
        return {
            "category_path": "/".join(dir_parts),
            "document_type": None,
            "domain": None,
            "document_number": dir_parts[-2] if len(dir_parts) >= 2 else None,
            "version_date": dir_parts[-1] if dir_parts else None,
        }

    # Levels after "Contenu": [doc_type, [domain, ...], doc_number, version_date]
    post_contenu = dir_parts[contenu_idx + 1 :]
    version_date = post_contenu[-1] if post_contenu else None
    document_number = post_contenu[-2] if len(post_contenu) >= 2 else None
    document_type = post_contenu[0] if post_contenu else None
    # Domain is the level immediately after document_type when there are ≥ 4 levels
    domain = post_contenu[1] if len(post_contenu) >= 4 else None

    # category_path: from "documents/" onward (excluding the filename)
    docs_idx = next(
        (i for i, p in enumerate(dir_parts) if p.lower() == "documents"), contenu_idx
    )
    category_path = "/".join(dir_parts[docs_idx:])

    return {
        "category_path": category_path,
        "document_type": document_type,
        "domain": domain,
        "document_number": document_number,
        "version_date": version_date,
    }


def _process_bofip_document(
    xml_content: bytes,
    html_content: bytes,
    file_path: str,
    model: str,
    telemetry: PerfTelemetry | None = None,
) -> list:
    """
    Process a single BOFiP document pair (``document.xml`` + ``data.html``), extract
    metadata and text, generate an embedding, and return a list containing the data
    row ready for database insertion.

    The whole ``data.html`` content is used as a single chunk to preserve the full
    context of each tax document (no further text splitting).

    BOFiP ``document.xml`` uses two XML namespaces:

    - **Dublin Core** (``dc:``) — title, date, creator, subjects, identifiers,
      and ``dc:relation`` elements that carry internal links/references to other
      BOFiP documents.
    - **BOFiP** (``bofip:``) — ``contenu_id`` (canonical identifier) and
      ``contenu_type`` (document category).

    Each ``dc:relation`` element has a ``type`` attribute (``"references"``,
    ``"isReferencedBy"``, ``"requires"``, or ``"isRequiredBy"``) and a text value
    structured as ``<Nature>.<DocumentType>:<identifier>``
    (e.g. ``"Contenu.Commentaire:389-PGP"``).  All relations are collected into a
    JSON array and stored in the ``links`` column.

    Args:
        xml_content (bytes): Raw bytes of ``document.xml``.
        html_content (bytes): Raw bytes of ``data.html``.
        file_path (str): Path of ``document.xml`` inside the archive, used to
            derive the taxonomy path.
        model (str): Embedding model identifier.

    Returns:
        list: A list containing a single tuple ready for insertion into the BOFIP table.

    Raises:
        PermissionDeniedError: Re-raised when the embedding API key is rejected.
    """
    DC = "{http://purl.org/dc/elements/1.1}"
    BOFIP_NS = "{https://bofip.impots.gouv.fr}"

    # ── XML metadata ────────────────────────────────────────────────────────
    with _telemetry_stage(telemetry, "parse"):
        xml_root = ET.fromstring(xml_content)

    title = xml_root.findtext(f".//{DC}title")
    publication_date = xml_root.findtext(f".//{DC}date")

    # dc:identifier appears twice: first is the document number, second is the URL
    identifiers = [el.text for el in xml_root.findall(f".//{DC}identifier") if el.text]
    document_number = None
    bofip_url = None
    for id_val in identifiers:
        if id_val.startswith("http"):
            bofip_url = id_val
        else:
            document_number = id_val

    # dc:subject may repeat; deduplicate while preserving order
    seen: set[str] = set()
    subjects = []
    for el in xml_root.findall(f".//{DC}subject"):
        if el.text and el.text not in seen:
            seen.add(el.text)
            subjects.append(el.text)

    # dc:relation contains internal links/references to other BOFiP documents.
    # Each element has a "type" attribute (e.g. "references", "isReferencedBy",
    # "requires", "isRequiredBy") and a text value structured as:
    #   <Nature>.<DocumentType>:<identifier>
    # e.g. "Contenu.Commentaire:389-PGP"
    links = []
    for el in xml_root.findall(f".//{DC}relation"):
        relation_type = el.get("type")
        value = el.text.strip() if el.text else None
        if not value:
            continue
        nature = None
        document_type = None
        doc_ref_id = None
        if "." in value and ":" in value:
            # Format: <Nature>.<DocumentType>:<identifier>
            # e.g. "Contenu.Autres annexes:1077-PGP"
            # Use partition to split on the first "." and first ":"
            before_dot, dot_found, after_dot = value.partition(".")
            if dot_found:
                doc_type_part, colon_found, identifier = after_dot.partition(":")
                if colon_found:
                    nature = before_dot
                    document_type = doc_type_part
                    doc_ref_id = identifier
        links.append(
            {
                "type": relation_type,
                "nature": nature,
                "document_type": document_type,
                "id": doc_ref_id,
                "value": value,
            }
        )

    contenu_id = xml_root.findtext(f".//{BOFIP_NS}contenu_id")
    contenu_type = xml_root.findtext(f".//{BOFIP_NS}contenu_type")

    # Canonical document ID: prefer contenu_id, then extract from URL, then fallback
    if contenu_id:
        doc_id = contenu_id
    elif bofip_url and "identifiant=" in bofip_url:
        doc_id = bofip_url.split("identifiant=")[-1]
    elif document_number:
        doc_id = document_number
    else:
        doc_id = (
            file_path.replace("/document.xml", "").replace("\\", "/").replace("/", "_")
        )

    # ── Path-derived taxonomy info ───────────────────────────────────────────
    path_info = _parse_bofip_path(file_path)
    category_path = path_info.get("category_path")

    # ── HTML → plain text ───────────────────────────────────────────────────
    with _telemetry_stage(telemetry, "parse"):
        soup = BeautifulSoup(html_content, "lxml")
    raw_text = soup.get_text(separator="\n", strip=True)
    lines = [line for line in raw_text.splitlines() if line.strip()]
    text_content = "\n".join(lines)
    data_to_insert = []
    with _telemetry_stage(telemetry, "chunking"):
        chunks = make_chunks(text=text_content)
    row_payload = []
    chunk_texts = []
    for k, text in enumerate(chunks):
        # ── Enriched chunk_text for embedding ───────────────────────────────────
        chunk_text_parts = []
        if title:
            chunk_text_parts.append(title)
        if contenu_type:
            chunk_text_parts.append(f"Type: {contenu_type}")
        if subjects:
            chunk_text_parts.append(f"Domaine: {', '.join(subjects)}")
        if category_path:
            chunk_text_parts.append(f"Chemin: {category_path}")
        if publication_date:
            chunk_text_parts.append(f"Date de publication: {publication_date}")
        if text:
            chunk_text_parts.append(text)
        chunk_text = "\n".join(chunk_text_parts)

        chunk_index = k + 1  # Start chunk numbering from 1
        chunk_id = f"{doc_id}_{chunk_index}"
        chunk_xxh64 = xxhash.xxh64(chunk_text.encode("utf-8"), seed=2025).hexdigest()

        new_data = (
            chunk_id,          # PRIMARY KEY
            doc_id,            # bofip:contenu_id (canonical identifier)
            chunk_index,       # k+1 (1 for the first chunk, 2 for the second, etc.)
            chunk_xxh64,       # xxhash of chunk_text
            title,             # dc:title
            contenu_id,        # bofip:contenu_id
            contenu_type,      # bofip:contenu_type
            document_number,   # first dc:identifier (e.g. "6551-PGP")
            bofip_url,         # second dc:identifier (source URL)
            publication_date,  # dc:date
            subjects,          # deduplicated dc:subject values
            category_path,     # full taxonomy path from the archive
            json.dumps(links, ensure_ascii=False),  # dc:relation links
            text,              # plain text extracted from data.html
            chunk_text,        # enriched text used for embedding
        )
        row_payload.append(new_data)
        chunk_texts.append(chunk_text)

    try:
        with _telemetry_stage(telemetry, "embedding"):
            embeddings_list = _embed_texts(chunk_texts=chunk_texts, model=model, telemetry=telemetry)
    except PermissionDeniedError as e:
        logger.error(f"PermissionDeniedError generating embedding for {doc_id}: {e}")
        raise

    for row_data, embeddings in zip(row_payload, embeddings_list):
        data_to_insert.append((*row_data, embeddings))

    if telemetry is not None:
        telemetry.add_counter("docs_processed", 1)
        telemetry.add_counter("chunks_produced", len(data_to_insert))

    return data_to_insert


def _process_bofip_tgz(
    tgz_path: str,
    table_name: str,
    checkpoint: CheckpointManager,
    last_processed_index: int,
    model: str,
    telemetry: PerfTelemetry | None = None,
):
    """
    Process a single BOFiP ``.tgz`` archive and insert all documents into the database.

    The function walks the archive looking for directories that contain both
    ``document.xml`` and ``data.html``. For each such directory it parses the XML
    metadata and the HTML content, generates a single embedding (whole-document chunk),
    and inserts the resulting row. A checkpoint is saved after each successful insertion
    so the run can be safely resumed from the last processed document.

    The archive file is removed once all documents have been processed successfully.

    Args:
        tgz_path (str): Absolute path to the ``.tgz`` archive.
        table_name (str): Database table to insert into (``"bofip"``).
        checkpoint (CheckpointManager): Checkpoint manager for this archive.
        last_processed_index (int): Index of the last successfully processed
            document (-1 = start from the beginning).
        model (str): Embedding model identifier.

    Raises:
        PermissionDeniedError: Re-raised immediately (unrecoverable API error).
        Exception: Any other unrecoverable error encountered during processing.
    """
    try:
        with tarfile.open(tgz_path, "r:gz") as tar:
            all_members = tar.getmembers()

            # Build a mapping: directory path → {filename: TarInfo member}
            dir_to_files: dict[str, dict[str, tarfile.TarInfo]] = {}
            for member in all_members:
                if not member.isfile():
                    continue
                norm_name = member.name.replace("\\", "/")
                parts = norm_name.split("/")
                dir_path = "/".join(parts[:-1])
                filename = parts[-1]
                dir_to_files.setdefault(dir_path, {})[filename] = member

            # Collect and sort all leaf directories containing both required files
            document_dirs = sorted(
                dir_path
                for dir_path, files in dir_to_files.items()
                if "document.xml" in files and "data.html" in files
            )

            total_docs = len(document_dirs)
            logger.info(
                f"Found {total_docs} document(s) in {os.path.basename(tgz_path)}, "
                f"starting from index {last_processed_index + 1}"
            )

            for idx, dir_path in enumerate(document_dirs):
                if idx <= last_processed_index:
                    continue

                xml_member = dir_to_files[dir_path]["document.xml"]
                html_member = dir_to_files[dir_path]["data.html"]
                xml_path = xml_member.name

                try:
                    xml_obj = tar.extractfile(xml_member)
                    html_obj = tar.extractfile(html_member)

                    if xml_obj is None or html_obj is None:
                        logger.warning(
                            f"Skipping {dir_path}: could not extract file object"
                        )
                        checkpoint.save(
                            idx, metadata={"dir": dir_path, "skipped": True}
                        )
                        continue

                    with xml_obj as xf, html_obj as hf:
                        xml_content = xf.read()
                        html_content = hf.read()

                    data_to_insert = _process_bofip_document(
                        xml_content=xml_content,
                        html_content=html_content,
                        file_path=xml_path,
                        model=model,
                        telemetry=telemetry,
                    )

                    if data_to_insert:
                        with _telemetry_stage(telemetry, "postgres_insert"):
                            insert_data(data=data_to_insert, table_name=table_name)
                        with _telemetry_stage(telemetry, "graph_upsert"):
                            upsert_bofip_node(data_to_insert)
                        if telemetry is not None:
                            telemetry.add_counter("rows_written", len(data_to_insert))

                    checkpoint.save(
                        idx,
                        metadata={
                            "dir": dir_path,
                            "doc_id": data_to_insert[0][1] if data_to_insert else None,
                        },
                    )

                    if idx > 0 and idx % 100 == 0:
                        gc.collect()

                except ET.ParseError as e:
                    logger.error(f"XML parse error for {dir_path}: {e}")
                    checkpoint.save(
                        idx, metadata={"dir": dir_path, "error": "parse_error"}
                    )
                    continue
                except PermissionDeniedError as e:
                    logger.error(
                        f"PermissionDeniedError processing {dir_path}: {e}"
                    )
                    raise
                except Exception as e:
                    logger.error(f"Error processing document at {dir_path}: {e}")
                    logger.error("Progress saved. Restart to resume from this point.")
                    raise

        # All documents processed successfully
        checkpoint.remove()
        logger.info(
            f"Successfully processed all {total_docs} documents "
            f"from {os.path.basename(tgz_path)}"
        )

    except Exception as e:
        logger.error(f"Error processing BOFiP archive {tgz_path}: {e}")
        raise
    finally:
        if not checkpoint.exists():
            remove_file(file_path=tgz_path)
            logger.info(f"Archive removed: {tgz_path}")
        else:
            logger.info(f"Archive kept for resume: {tgz_path}")
        gc.collect()


def process_bofip_files(
    table_name: str,
    model: str = EMBEDDING_MODEL,
    telemetry: PerfTelemetry | None = None,
):
    """
    Process all BOFiP ``.tgz`` archives found in the configured download folder.

    BOFiP archives come in two types:

    - **Stock** files (``bofip_stock_*.tgz``): Full snapshot of current doctrinal
      content. Processed first; triggers a table refresh when starting from scratch
      so that documents deleted from the official corpus are removed from the database.
    - **Flux** files (``bofip_flux_*.tgz``): Incremental weekly updates. Processed
      after stock files in chronological (filename) order.

    Each archive contains a hierarchical ``BOFiP/`` directory where every leaf
    directory holds exactly two files: ``document.xml`` (metadata) and ``data.html``
    (document content). The whole HTML is preserved as a single chunk.

    A checkpoint file is saved after each document so that interrupted runs can be
    resumed without reprocessing already-inserted documents.

    Args:
        table_name (str): Name of the database table to insert into (``"bofip"``).
        model (str): Embedding model identifier. Defaults to ``EMBEDDING_MODEL``.
    """
    config = load_config(config_file_path=config_file_path)
    data_sources = config.get(table_name.lower(), {})

    for data_source, attributes in data_sources.items():
        base_folder = os.path.join(BASE_PATH, attributes.get("download_folder", ""))

        if not os.path.isdir(base_folder):
            logger.warning(f"BOFiP download folder not found: {base_folder}")
            continue

        # Collect all BOFiP tgz archives in the folder
        all_tgz = sorted(
            f
            for f in os.listdir(base_folder)
            if f.startswith("bofip_") and f.endswith(".tgz")
        )

        if not all_tgz:
            logger.info(f"No BOFiP archives found in {base_folder}")
            continue

        # Stock files first (full baseline), then flux files (incremental)
        stock_files = sorted(f for f in all_tgz if "_stock_" in f)
        flux_files = sorted(f for f in all_tgz if "_flux_" in f)
        ordered_files = stock_files + flux_files

        logger.info(
            f"Found {len(stock_files)} stock and {len(flux_files)} flux "
            f"BOFiP archive(s) in {base_folder}"
        )

        for tgz_file in ordered_files:
            tgz_path = os.path.join(base_folder, tgz_file)
            checkpoint = CheckpointManager(tgz_path)
            last_processed_index = checkpoint.load()
            is_stock = "_stock_" in tgz_file

            logger.info(
                f"Processing BOFiP archive: {tgz_file} "
                f"(type={'stock' if is_stock else 'flux'}, "
                f"resuming from index {last_processed_index + 1})"
            )

            if last_processed_index == -1 and is_stock:
                # First run with a stock file: refresh the table so stale documents
                # that were removed from the official corpus are cleaned up.
                logger.info(
                    f"Refreshing table '{table_name}' before processing stock file"
                )
                with refresh_table(table_name, model):
                    _process_bofip_tgz(
                        tgz_path=tgz_path,
                        table_name=table_name,
                        checkpoint=checkpoint,
                        last_processed_index=last_processed_index,
                        model=model,
                        telemetry=telemetry,
                    )
            else:
                _process_bofip_tgz(
                    tgz_path=tgz_path,
                    table_name=table_name,
                    checkpoint=checkpoint,
                    last_processed_index=last_processed_index,
                    model=model,
                    telemetry=telemetry,
                )


def process_bofip_selected_files(
    table_name: str,
    selected_folder: str,
    model: str = EMBEDDING_MODEL,
    telemetry: PerfTelemetry | None = None,
):
    """Process BOFiP files from selected folder structure built by smart_process_tax.sh."""
    document_dirs = []
    for root_dir, _, files in os.walk(selected_folder):
        file_set = set(files)
        if "document.xml" in file_set and "data.html" in file_set:
            document_dirs.append(root_dir)

    document_dirs = sorted(document_dirs)

    if not document_dirs:
        logger.info(f"No selected BOFiP documents found in {selected_folder}")
        return

    logger.info(
        f"Processing {len(document_dirs)} selected BOFiP document(s) from {selected_folder}"
    )

    work_items = []
    for dir_path in document_dirs:
        xml_path = os.path.join(dir_path, "document.xml")
        html_path = os.path.join(dir_path, "data.html")
        rel_xml_path = os.path.relpath(xml_path, selected_folder).replace(os.sep, "/")
        work_items.append((xml_path, html_path, rel_xml_path))

    if ENABLE_PARALLEL_PROCESSING and MAX_WORKERS > 1:
        for i in range(0, len(work_items), max(1, BATCH_SIZE_DOCS)):
            batch = work_items[i : i + max(1, BATCH_SIZE_DOCS)]
            with ProcessPoolExecutor(max_workers=MAX_WORKERS) as executor:
                futures = [
                    executor.submit(
                        _prepare_bofip_payload_from_paths,
                        xml_path,
                        html_path,
                        rel_xml_path,
                        model,
                    )
                    for xml_path, html_path, rel_xml_path in batch
                ]

                for future in futures:
                    try:
                        result_obj = future.result()
                        data_to_insert, worker_stage_seconds = _extract_bofip_result_payload(result_obj)
                        _merge_worker_stage_seconds(telemetry, worker_stage_seconds)

                        if data_to_insert:
                            if telemetry is not None:
                                telemetry.add_counter("docs_processed", 1)
                                telemetry.add_counter("chunks_produced", len(data_to_insert))
                            with _telemetry_stage(telemetry, "postgres_insert"):
                                insert_data(data=data_to_insert, table_name=table_name)
                            with _telemetry_stage(telemetry, "graph_upsert"):
                                upsert_bofip_node(data_to_insert)
                            if telemetry is not None:
                                telemetry.add_counter("rows_written", len(data_to_insert))
                    except ET.ParseError as e:
                        logger.error("XML parse error for selected BOFiP document: %s", e)
                        continue
                    except PermissionDeniedError:
                        raise
                    except Exception as e:
                        logger.error("Error processing selected BOFiP document: %s", e)
                        raise
            gc.collect()
    else:
        for idx, (xml_path, html_path, rel_xml_path) in enumerate(work_items):
            try:
                result_obj = _prepare_bofip_payload_from_paths(
                    xml_path=xml_path,
                    html_path=html_path,
                    rel_xml_path=rel_xml_path,
                    model=model,
                )
                data_to_insert, worker_stage_seconds = _extract_bofip_result_payload(result_obj)
                _merge_worker_stage_seconds(telemetry, worker_stage_seconds)

                if data_to_insert:
                    if telemetry is not None:
                        telemetry.add_counter("docs_processed", 1)
                        telemetry.add_counter("chunks_produced", len(data_to_insert))
                    with _telemetry_stage(telemetry, "postgres_insert"):
                        insert_data(data=data_to_insert, table_name=table_name)
                    with _telemetry_stage(telemetry, "graph_upsert"):
                        upsert_bofip_node(data_to_insert)
                    if telemetry is not None:
                        telemetry.add_counter("rows_written", len(data_to_insert))

                if idx > 0 and idx % 100 == 0:
                    gc.collect()

            except ET.ParseError as e:
                logger.error(f"XML parse error for selected BOFiP document at {xml_path}: {e}")
                continue
            except PermissionDeniedError:
                raise
            except Exception as e:
                logger.error(f"Error processing selected BOFiP document at {xml_path}: {e}")
                raise


def process_data(table_name: str, streaming: bool = True, model: str = EMBEDDING_MODEL):
    """
    Processes data files located in the specified base folder according to its type.
    Depending on the value of `base_folder`, this function performs several operations.

    Args:
        table_name (str): The name of the table to process.
        streaming (bool, optional): If True, processes DILA archive files in streaming mode, without extraction (default: True).
        If False, extracts the archive files before processing.
        model (str, optional): The model to use for processing (default: EMBEDDING_MODEL).
    """
    telemetry = PerfTelemetry(run_name=f"process_{table_name.lower()}")
    telemetry.maybe_start_profilers()
    try:
        with _telemetry_stage(telemetry, "smart_preprocessing"):
            _run_smart_process_tax_if_needed(table_name=table_name)

        config = load_config(config_file_path=config_file_path)
        data_sources = config.get(table_name.lower(), {})
        for data_source, attributes in data_sources.items():
            base_folder = os.path.join(BASE_PATH, attributes.get("download_folder", ""))

            if attributes.get("type") == "bofip":
                selected_bofip_folder = _get_selected_folder(table_name=table_name)
                if not _has_bofip_selected_documents(selected_folder=selected_bofip_folder):
                    raise FileNotFoundError(
                        f"No selected BOFiP document.xml/data.html pairs found in {selected_bofip_folder}"
                    )
                logger.info(
                    f"Processing selected BOFiP files located in: {selected_bofip_folder}"
                )
                process_bofip_selected_files(
                    table_name=table_name,
                    selected_folder=selected_bofip_folder,
                    model=model,
                    telemetry=telemetry,
                )
                logger.info(
                    f"Selected BOFiP files in {selected_bofip_folder} successfully processed"
                )

            elif attributes.get("type") == "dila_folder":
                selected_dila_folder = _get_selected_folder(table_name=table_name)
                if not _directory_has_suffix(selected_dila_folder, ".xml"):
                    raise FileNotFoundError(
                        f"No selected DILA XML files found in {selected_dila_folder}"
                    )
                logger.info(
                    f"Processing selected DILA XML files located in: {selected_dila_folder}"
                )
                process_dila_xml_files(
                    source_path=selected_dila_folder,
                    streaming=False,
                    model=model,
                    telemetry=telemetry,
                )
                logger.info(
                    f"Selected DILA files in {selected_dila_folder} successfully processed"
                )
            else:
                logger.error(f"Unknown base folder '{base_folder}' for processing data.")
                raise ValueError(
                    f"Unknown base folder '{base_folder}' for processing data."
                )
    except Exception as exc:
        telemetry.add_error("process_data", str(exc))
        raise
    finally:
        profiler_results = telemetry.maybe_stop_profilers()
        report = telemetry.finalize(
            metadata={
                "table_name": table_name,
                "streaming": streaming,
                "parallel_processing": ENABLE_PARALLEL_PROCESSING,
                "max_workers": MAX_WORKERS,
                "batch_size_docs": BATCH_SIZE_DOCS,
            }
        )
        if profiler_results.get("memory"):
            report["memory"] = profiler_results["memory"]
        if profiler_results.get("cprofile"):
            report["cprofile_top"] = profiler_results["cprofile"]
        telemetry.write_report(report=report, suffix=table_name.lower())


def process_all_data(
    source_map: str = SOURCE_MAP, model: str = EMBEDDING_MODEL, streaming: bool = True
):
    """
    Processes all data tables defined in the source map.

    Args:
        source_map (str): Mapping of data sources to be processed.
        model (str): The model to use for processing.
        streaming (bool): Whether to process DILA files in streaming mode.

    Note:
        This function iterates over each table name in the provided source map
        and calls the `process_data` function to handle the processing of each table.
    """
    for table_name in source_map:
        process_data(table_name=table_name, model=model, streaming=streaming)
