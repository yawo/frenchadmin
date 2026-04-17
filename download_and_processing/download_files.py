import json
import os
import shutil
from datetime import datetime
from urllib.error import HTTPError
from urllib.request import urlopen

import requests
from bs4 import BeautifulSoup

from config import BASE_PATH, EMBEDDING_MODEL, config_file_path, data_history_path, get_logger
from utils import (
    correct_wrong_column_contents,
    download_file,
    extract_and_remove_tar_file,
    load_config,
    load_data_history,
)

from .files_processing import process_data

logger = get_logger(__name__)


def download_and_optionally_process_files(
    table_name: str,
    process: bool = False,
    streaming: bool = True,
    model: str = EMBEDDING_MODEL,
):
    """
    Download and optionally process files based on the data configuration type.
    Downloads files, extracts archives if needed, optionally processes data using specified model,
    and updates download history.
    Args:
        table_name: Name of the data source to process
        process: Flag to indicate whether to process the data after download (default: False)
        streaming: Flag to indicate whether to stream extraction of tar files, for DILA files only (default: True)
        model: Model name for data processing (default: EMBEDDING_MODEL)
    """

    config = load_config(config_file_path=config_file_path)
    log = load_data_history(data_history_path=data_history_path)
    try:
        data_sources = config.get(table_name.lower(), {})
        len_data_sources = len(data_sources.items())
        for data_source_index, (
            data_source,
            attributes,
        ) in enumerate(
            data_sources.items()
        ):  # As a data source can have multiple files to download
            if attributes.get("type") == "dila_folder":
                url = attributes.get("download_url", "")
                download_folder = os.path.join(
                    BASE_PATH, attributes.get("download_folder", "")
                )
                # Ensure the download folder exists
                os.makedirs(download_folder, exist_ok=True)
                try:
                    last_downloaded_file = log.get(data_source).get(
                        "last_downloaded_file", ""
                    )
                except Exception:
                    last_downloaded_file = ""

                try:
                    response = requests.get(url)
                    response.raise_for_status()

                    # Parse the HTML content using BeautifulSoup
                    soup = BeautifulSoup(response.text, "html.parser")

                    # Find all links that end with ".tar.gz"
                    links = soup.find_all("a", href=True)

                    tar_gz_files = sorted(
                        [
                            link["href"]
                            for link in links
                            if link["href"].endswith(".tar.gz")
                        ]
                    )
                    # Placing the freemium file at the beginning
                    try:
                        freemium_file = next(
                            (
                                file
                                for file in tar_gz_files
                                if file.lower().startswith("freemium")
                            ),
                            None,
                        )
                        tar_gz_files.remove(freemium_file)
                        tar_gz_files.insert(0, freemium_file)
                    except ValueError:
                        logger.warning(f"There is no freemium file in {url}")

                    logger.debug(
                        f"{len(tar_gz_files)} tar.gz files found in {url}: {tar_gz_files}"
                    )

                    if last_downloaded_file in tar_gz_files:
                        last_file_index = tar_gz_files.index(last_downloaded_file)
                        logger.info(
                            f"Last downloaded file is {last_downloaded_file} according to the data history"
                        )
                    else:
                        last_file_index = -1

                    if last_file_index == len(tar_gz_files) - 1:
                        logger.info("No new files to download")
                        return

                    else:
                        for filename in tar_gz_files[
                            last_file_index + 1 :
                        ]:  # As we already downloaded the last file, we start from the next file
                            file_url = os.path.join(url, filename)
                            download_path = os.path.join(download_folder, filename)

                            download_file(url=file_url, destination_path=download_path)

                            if not streaming:
                                extract_and_remove_tar_file(
                                    file_path=download_path,
                                    extract_path=download_folder,
                                )
                            if process:
                                # Process the downloaded file and remove the folder after processing
                                process_data(
                                    table_name=table_name,
                                    streaming=streaming,
                                    model=model,
                                )

                                logger.info(
                                    f"Successfully downloaded and processed {filename}"
                                )
                            else:
                                logger.info(f"Successfully downloaded {filename}")

                            # Update the last download file and date in the log
                            log[data_source] = {
                                "last_downloaded_file": filename,
                                "last_download_date": datetime.now().strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                ),
                            }

                            with open(data_history_path, "w") as file:
                                json.dump(log, file, indent=4)
                            logger.info(
                                f"Log config file successfully updated to {data_history_path}"
                            )

                except Exception as e:
                    logger.error(f"Error downloading files: {e}")
                    raise e

                try:
                    # Correct wrong 'category' column contents in the 'legi' table after processing all files
                    if process and table_name.lower() == "legi":
                        correct_wrong_column_contents(
                            table_name="legi",
                            column_to_correct="category",
                            column_helper="title",
                        )
                except Exception as e:
                    logger.error(
                        f"Error correcting 'category' column in 'legi' table: {e}"
                    )
                    raise e
            elif attributes.get("type") == "bofip":
                
                url = attributes.get("download_url", "")
                download_folder = os.path.join(
                    BASE_PATH, attributes.get("download_folder", "")
                )                  

                os.makedirs(download_folder, exist_ok=True)
                logger.info(f"Downloading '{data_source}' from {url}...")
                response = requests.get(url)
                csv = response.content.decode("utf-8")
                # Open CSV and check files against last_downloaded_file_list
                '''
                Nom du fichier;Date de début;Date de fin;Téléchargement;Type;Contenu;Empreinte
                bofip_flux_live_20260129_20260204.tgz;2026-01-29;2026-02-04;https://bofip.impots.gouv.fr/opendata/flux/6;flux;6 nouvelles publications doctrinales.;https://bofip.impots.gouv.fr/opendata/empreinte/flux/6
                bofip_flux_live_20260122_20260128.tgz;2026-01-22;2026-01-28;https://bofip.impots.gouv.fr/opendata/flux/5;flux;3 nouvelles publications doctrinales. Mise à jour du plan de classement.;https://bofip.impots.gouv.fr/opendata/empreinte/flux/5
                bofip_flux_live_20260101_20260107.tgz;2026-01-01;2026-01-07;https://bofip.impots.gouv.fr/opendata/flux/2;flux;Aucune publication sur cette période.;https://bofip.impots.gouv.fr/opendata/empreinte/flux/2
                bofip_flux_live_20260205_20260211.tgz;2026-02-05;2026-02-11;https://bofip.impots.gouv.fr/opendata/flux/7;flux;12 nouvelles publications doctrinales. Mise à jour du plan de classement.;https://bofip.impots.gouv.fr/opendata/empreinte/flux/7
                bofip_flux_live_20260115_20260121.tgz;2026-01-15;2026-01-21;https://bofip.impots.gouv.fr/opendata/flux/4;flux;11 nouvelles publications doctrinales. Mise à jour du plan de classement.;https://bofip.impots.gouv.fr/opendata/empreinte/flux/4
                bofip_stock_live_20260128.tgz;;2026-01-28;https://bofip.impots.gouv.fr/opendata/stock/1;stock;Contenu doctrinal en vigueur au 28/01/2026.;https://bofip.impots.gouv.fr/opendata/empreinte/stock/1
                bofip_flux_live_20260108_20260114.tgz;2026-01-08;2026-01-14;https://bofip.impots.gouv.fr/opendata/flux/3;flux;4 nouvelles publications doctrinales. Mise à jour du plan de classement.;https://bofip.impots.gouv.fr/opendata/empreinte/flux/3
                '''
                csvLines = csv.split("\n")

                # Sort zip with url, sorted on filename
                subtypes = ["flux", "stock"]
                for subtype in subtypes:                  
                    try:
                        last_downloaded_file = log.get(data_source).get(
                            f"last_downloaded_{subtype}_file", "")
                    except Exception:
                        last_downloaded_file = "" 

                    tar_gz_files = sorted(
                        [
                            {"filename": line.split(";")[0], "url": line.split(";")[3]}
                            for line in csvLines
                            if line.split(";")[0].endswith(".tgz") and line.split(";")[4] == subtype
                        ], key=lambda x: x["filename"]
                    )
                    
                    logger.debug(
                        f"{len(tar_gz_files)} {subtype} tar.gz files found in {url}: {tar_gz_files}"
                    )

                    if last_downloaded_file in tar_gz_files:
                        last_file_index = tar_gz_files.index(last_downloaded_file)
                        logger.info(
                            f"Last downloaded {subtype} file is {last_downloaded_file} according to the data history"
                        )
                    else:
                        last_file_index = -1

                    if last_file_index == len(tar_gz_files) - 1:
                        logger.info(f"No new {subtype} files to download")
                        return

                    else:
                        for file in tar_gz_files[
                            last_file_index + 1 :
                        ]:  # As we already downloaded the last file, we start from the next file
                            filename = file.get("filename")
                            url=file.get("url")
                            download_path = os.path.join(download_folder, filename)
                            download_file(url=url, destination_path=download_path)

                            if not streaming:
                                extract_and_remove_tar_file(
                                    file_path=download_path,
                                    extract_path=download_folder,
                                )
                            if process:
                                # Process the downloaded file and remove the folder after processing
                                process_data(
                                    table_name=table_name,
                                    streaming=streaming,
                                    model=model,
                                )

                                logger.info(
                                    f"Successfully downloaded and processed {filename}"
                                )
                            else:
                                logger.info(f"Successfully downloaded {filename}")

                            # Update the last download file and date in the log
                            log[data_source] = {
                                "last_downloaded_file": filename,
                                "last_download_date": datetime.now().strftime(
                                    "%Y-%m-%d %H:%M:%S"
                                ),
                            }

                            with open(data_history_path, "w") as file:
                                json.dump(log, file, indent=4)
                            logger.info(
                                f"Log config file successfully updated to {data_history_path}"
                            )

            else:
                logger.error(f"Unknown type {attributes.get('type')} for {data_source}")

    except Exception as e:
        logger.error(f"Error processing {data_source}: {e}")
        raise e


def download_and_optionally_process_all_files(
    process: bool = False,
    streaming: bool = True,
    model: str = EMBEDDING_MODEL,
):
    """
    Downloads and optionally processes all files listed in the configuration file.
    This function iterates through each data source defined in the configuration,
    downloads the files, optionally processes them, and updates the history log by using `download_and_process_files()`.

    Args:
        process (bool): Flag to indicate whether to process the data after download (default: False).
        model (str): Model name for data processing (default: EMBEDDING_MODEL).
    """
    config = load_config(config_file_path=config_file_path)

    for table_name in config.keys():
        if process:
            logger.info(f"Downloading and processing {table_name}...")
        else:
            logger.info(f"Downloading {table_name}...")
        download_and_optionally_process_files(
            table_name=table_name,
            process=process,
            streaming=streaming,
            model=model,
        )
