import json
import os
import re
import time
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Generator, Optional

import numpy as np
import xxhash
from langchain.text_splitter import RecursiveCharacterTextSplitter
from sentence_transformers import SentenceTransformer
from tqdm import tqdm
from transformers import AutoTokenizer

from config import (
    EMBEDDING_MODEL,
    SERVICE_PUBLIC_PART_DATA_FOLDER,
    SERVICE_PUBLIC_PRO_DATA_FOLDER,
    TRAVAIL_EMPLOI_DATA_FOLDER,
    get_logger,
)

from .data_helpers import doc_to_chunk
from .sheets_parser import RagSource

logger = get_logger(__name__)

_tokenizer_cache = {}  # Cache of loaded tokenizers keyed by model name
_embedding_model_cache = {}  # Cache of loaded SentenceTransformer models keyed by model name


def _get_embedding_model(model: str = EMBEDDING_MODEL) -> SentenceTransformer:
    """
    Returns a cached SentenceTransformer model, downloading it from HuggingFace if needed.

    Args:
        model (str): HuggingFace model identifier. Defaults to EMBEDDING_MODEL.

    Returns:
        SentenceTransformer: The loaded embedding model.
    """
    global _embedding_model_cache
    if model not in _embedding_model_cache:
        logger.info(f"Loading embedding model '{model}' from HuggingFace...")
        _embedding_model_cache[model] = SentenceTransformer(model, trust_remote_code=True)
    return _embedding_model_cache[model]


class CorpusHandler(ABC):
    def __init__(self, name, corpus):
        self._name = name
        self._corpus = corpus

    @classmethod
    def create_handler(cls, corpus_name: str, corpus: list[dict]) -> "CorpusHandler":
        """Get the appropriate handler subclass from the string corpus name."""
        corpuses = {
            TRAVAIL_EMPLOI_DATA_FOLDER.split("/")[-1]: SheetChunksHandler,
            SERVICE_PUBLIC_PRO_DATA_FOLDER.split("/")[-1]: SheetChunksHandler,
            SERVICE_PUBLIC_PART_DATA_FOLDER.split("/")[-1]: SheetChunksHandler,
        }
        if corpus_name not in corpuses:
            raise ValueError(f"Corpus '{corpus_name}' is not recognized")
        return corpuses[corpus_name](corpus_name, corpus)

    def iter_docs(
        self, batch_size: int, desc: str = None
    ) -> Generator[list, None, None]:
        if not desc:
            desc = f"Processing corpus: {self._name}..."

        corpus = self._corpus
        num_chunks = len(corpus) // batch_size
        if len(corpus) % batch_size != 0:
            num_chunks += 1

        for i in tqdm(range(num_chunks), desc=desc):
            start_idx = i * batch_size
            end_idx = min(start_idx + batch_size, len(corpus))
            yield corpus[start_idx:end_idx]

    def iter_docs_embeddings(
        self, batch_size: int, model: str = EMBEDDING_MODEL
    ) -> Generator[tuple[list], None, None]:
        desc = f"Processing corpus {self._name} with embeddings..."
        for batch in self.iter_docs(batch_size=batch_size, desc=desc):
            batch_embeddings = generate_embeddings_with_retry(
                data=[self.doc_to_chunk(x) for x in batch], attempts=5
            )
            # batch_embeddings = generate_embeddings_with_retry(
            #     data=[x.get("chunk_text") for x in batch], attempts=5, model=model
            # ) # For API insertion
            if len([x for x in batch_embeddings if x is not None]) == 0:
                continue
            yield batch, batch_embeddings

    @abstractmethod
    def doc_to_chunk(self, doc: dict) -> str:
        raise NotImplementedError("Subclasses should implement this!")


class SheetChunksHandler(CorpusHandler):
    def doc_to_chunk(self, doc: dict) -> str | None:
        """Converts a document dictionary into a single formatted string.

        This method combines the title, context, introduction, and text fields
        from a document dictionary into a unified string. It intelligently includes
        the introduction only if it is not already part of the main text to
        avoid redundancy.

        Args:
            doc (dict): A dictionary representing a document. Expected keys include
                'title', 'text', and optionally 'context' and 'introduction'.

        Returns:
            str: A single string representing the formatted document chunk.
        """
        context = ""
        if doc.get("context"):
            context = "  ( > ".join(doc["context"]) + ")"
        if doc.get("introduction") not in doc["text"]:
            chunk_text = "\n".join(
                [doc["title"] + context, doc["introduction"], doc["text"]]
            )
        else:
            chunk_text = "\n".join([doc["title"] + context, doc["text"]])

        return chunk_text


def generate_embeddings(
    data: str | list[str], model: str = EMBEDDING_MODEL
) -> list[float]:
    """
    Generates embeddings for a given text using a HuggingFace model downloaded locally.

    Args:
        data (str or list[str]): The input to generate embeddings for.
        model (str, optional): The HuggingFace model identifier. Defaults to EMBEDDING_MODEL.

    Returns:
        list[list[float]]: A list of embedding vectors for the input text(s).
    """
    if isinstance(data, str):
        data = [data]
    embedding_model = _get_embedding_model(model)
    vectors = embedding_model.encode(data, convert_to_numpy=False)
    return [v if isinstance(v, list) else list(v) for v in vectors]


def generate_embeddings_with_retry(
    data: str | list[str],
    attempts: int = 5,
    model: str = EMBEDDING_MODEL,
    time_sleep: int = 60,
) -> list[float]:
    """
    Generate embeddings for the provided data with retry mechanism.

    This function attempts to generate embeddings using the local HuggingFace model
    and retries in case of failures.

    Args:
        data (str | list[str]): The text data to generate embeddings for.
            Can be a single string or a list of strings.
        attempts (int, optional): Maximum number of retry attempts. Defaults to 5.
        model (str, optional): The HuggingFace embedding model to use. Defaults to EMBEDDING_MODEL.
        time_sleep (int, optional): Seconds to wait between retries. Defaults to 60.

    Returns:
        list[list[float]]: The generated embeddings as a list of float vectors.

    Raises:
        Exception: If embedding generation fails after all retry attempts.
    """

    for attempt in range(attempts):  # Retry embedding up to {attempts} times
        try:
            embeddings = generate_embeddings(data=data, model=model)
            return embeddings
        except Exception as e:
            if attempt == attempts - 1:  # If this is the last attempt
                logger.error(
                    f"Error generating embeddings for : {str(data)[:200]} ... Error: {e}. Maximum retries reached ({attempts}). Raising exception."
                )
                raise
            logger.error(
                f"Error generating embeddings for : {str(data)[:200]} ... Error: {e}. Retrying in {time_sleep} seconds (attempt {attempt + 1}/{attempts})"
            )
            time.sleep(time_sleep)  # Waiting {time_sleep} seconds before retrying


def _get_length_function(length_function: str):
    """
    Returns the appropriate length function based on the provided string identifier.

    Args:
        length_function (str): The identifier for the desired length function.
            Use "len" for character-based length, "bge_m3_tokenizer" for backwards
            compatibility with the BAAI/bge-m3 tokenizer, or any HuggingFace model
            name to load the corresponding tokenizer.
    """
    global _tokenizer_cache

    if length_function == "len":
        return len

    # "bge_m3_tokenizer" is kept for backwards compatibility
    model_name = "BAAI/bge-m3" if length_function == "bge_m3_tokenizer" else length_function
    if model_name not in _tokenizer_cache:
        _tokenizer_cache[model_name] = AutoTokenizer.from_pretrained(model_name)
    tokenizer = _tokenizer_cache[model_name]
    return lambda text: len(tokenizer.encode(text))


def make_chunks(
    text: str,
    chunk_size: int = 1500,
    chunk_overlap: int = 0,
    length_function=EMBEDDING_MODEL,
) -> list[str]:
    """
    Splits the input text into overlapping chunks using a recursive character-based text splitter.
    Args:
        text (str): The input text to be split into chunks.
        chunk_size (int, optional): The maximum size of each chunk.
        chunk_overlap (int, optional): The number of overlapping characters between consecutive chunks.
        length_function (str or callable, optional): Controls text length measurement. Accepts "len"
            for character count, "bge_m3_tokenizer" for backwards compatibility, or any HuggingFace
            model name to use the corresponding tokenizer. Defaults to EMBEDDING_MODEL.
    Returns:
        List[str]: A list of text chunks generated from the input text.
    """
    if not text:
        logger.debug("Empty text provided for chunking.")
        return []

    length_fn = _get_length_function(length_function)

    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", " ", ""],
        length_function=length_fn,
    )
    chunks = text_splitter.split_text(text)
    return chunks


def make_directory_text(
    nom: str,
    mission: Optional[str],
    responsables: Optional[list],
    adresses: Optional[list],
) -> str:
    """
    Generates a concatenated string from provided entity information and a list of addresses for embedding purposes.

    Args:
        nom (str): The name of the entity.
        mission (str): The mission or purpose of the entity.
        types (str): The type(s) or category of the entity.
        responsables (list): A list of dictionaries, each representing a responsible person with possible keys:
            - 'fonction' (str, optional): The function or role of the person.
            - 'civilite' (str, optional): The title or salutation of the person.
            - 'prenom' (str, optional): The first name of the person.
            - 'nom' (str, optional): The last name of the person.
            - 'grade' (str, optional): The grade or rank of the person.
            - 'telephone' (str, optional): The telephone number of the person.
            - 'adresse_courriel' (list, optional): A list of email addresses, each represented as a dictionary with keys:
        adresses (list): A list of dictionaries, each representing an address with possible keys:
            - 'complement1' (str, optional): Additional address information.
            - 'complement2' (str, optional): Additional address information.
            - 'numero_voie' (str, optional): Street number.
            - 'code_postal' (str, optional): Postal code.
            - 'nom_commune' (str, optional): City or commune name.
            - 'pays' (str, optional): Country name.

    Returns:
        str: A single string containing the concatenated and formatted information, suitable for embedding or search optimization.
    """
    adresses_to_concatenate = []
    try:
        for adresse in adresses:
            adresses_to_concatenate.append(
                f" {adresse.get('complement1', '')} {adresse.get('complement2', '')} {adresse.get('numero_voie', '')}, {adresse.get('code_postal', '')} {adresse.get('nom_commune', '')} {adresse.get('pays', '')}".strip()
            )

        # Concatenate all addresses in order to add them to the data to embed
        adresses_to_concatenate = " ".join(adresses_to_concatenate)
    except Exception:
        pass

    responsables_to_concatenate = []
    try:
        for responsable in responsables:
            resp = f"{responsable.get('fonction', '')}"
            if responsable.get("personne", {}):
                personne = responsable.get("personne", {})
                resp += f" : {personne.get('civilite', '')} {personne.get('prenom', '')} {personne.get('nom', '')}"
                if personne.get("grade", ""):
                    resp += f" ({personne.get('grade', '')})"
            responsables_to_concatenate.append(resp)
        responsables_to_concatenate = ".\n".join(responsables_to_concatenate)
    except Exception:
        pass

    # Text to embed in order to makes the search more efficient
    fields = [
        nom,
        mission if mission else "",
        responsables_to_concatenate if responsables_to_concatenate else "",
    ]  # adresses not added for now
    text_to_embed = ". ".join([f for f in fields if f]).strip()

    return text_to_embed


def make_chunks_sheets(
    storage_dir: str,
    structured=True,
    chunk_size=1024,
    chunk_overlap=0,
    length_function=EMBEDDING_MODEL,
) -> None:
    """
    Chunkify sheets and save chunks to a JSON file.

    Args:
        storage_dir (str): Directory where the sheets are stored.
        structured (bool, optional): Whether the sheets are structured. Defaults to True.
        chunk_size (int, optional): Size of each chunk. Defaults to 1500.
        chunk_overlap (int, optional): Overlap between chunks. Defaults to 0.
        length_function (str or callable, optional): Controls text length measurement. Accepts "len"
            for character count, "bge_m3_tokenizer" for backwards compatibility, or any HuggingFace
            model name to use the corresponding tokenizer. Defaults to EMBEDDING_MODEL.
    """

    if storage_dir is None:
        raise ValueError(
            "You must give a datas directory to chunkify in the param 'storage_dir'."
        )

    sheets = RagSource.get_sheets(
        storage_dir=storage_dir, structured=structured
    )  # list of dicts built from XML files

    length_fn = _get_length_function(length_function)

    chunks = []
    text_splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", " ", ""],
        length_function=length_fn,
    )
    hashes = []
    info = defaultdict(lambda: defaultdict(list))
    for data in sheets:
        texts = data["text"]
        surtitle = data["surtitle"]

        if not texts:
            continue
        if surtitle in ("Dossier", "Recherche guidée"):
            # TODO: can be used for cross-reference, see also <LienInterne>
            continue

        if structured:
            s = [x["text"] for x in texts]
        else:
            s = texts

        info[surtitle]["len"].append(len(" ".join(s).split()))
        index = 1  # chunk index starting from 1
        for natural_chunk in texts:
            if isinstance(natural_chunk, dict):
                natural_text_chunk = natural_chunk["text"]
            else:
                natural_text_chunk = natural_chunk

            for fragment in text_splitter.split_text(natural_text_chunk):
                if not fragment:
                    logger.warning("Warning: empty fragment")
                    continue

                info[surtitle]["chunk_len"].append(len(fragment.split()))

                chunk = {
                    **data,
                    "chunk_index": index,
                    "text": fragment,  # overwrite previous value
                }

                chunk["chunk_text"] = doc_to_chunk(
                    doc=chunk
                )  # concatenate title, context, (intro if not in text) and text

                chunk["context"] = (
                    natural_chunk["context"] if isinstance(natural_chunk, dict) else []
                )

                # add an unique hash
                h = xxhash.xxh64(chunk["chunk_text"].encode(), seed=2025).hexdigest()

                if h in hashes:
                    logger.debug(
                        f"Warning: duplicate chunk => {chunk['sid']} : {chunk['chunk_text'][:100]}..."
                    )
                    continue
                hashes.append(h)
                chunk["chunk_xxh64"] = h

                chunks.append(chunk)
                index += 1

    json_file_target = os.path.join(storage_dir, "sheets_as_chunks.json")
    with open(json_file_target, mode="w", encoding="utf-8") as f:
        json.dump(chunks, f, ensure_ascii=False, indent=4)

    info_summary = ""
    for k, v in info.items():
        v_len = v["len"]
        v_chunk_len = v["chunk_len"]
        template = "{}: {:.0f} ± {:.0f}    max:{} min:{}\n"
        info_summary += f"### {k}\n"
        info_summary += f"total doc: {len(v_len)}\n"
        info_summary += template.format(
            "mean length", np.mean(v_len), np.std(v_len), np.max(v_len), np.min(v_len)
        )
        info_summary += f"total chunk: {len(v_chunk_len)}\n"
        info_summary += template.format(
            "mean chunks length",
            np.mean(v_chunk_len),
            np.std(v_chunk_len),
            np.max(v_chunk_len),
            np.min(v_chunk_len),
        )
        info_summary += "\n"

    logger.info(f"Chunks created in : {str(json_file_target)}")


def _dole_cut_file_content(text: str):
    """
    Splits legal text into individual articles based on article numbering patterns.

    Args:
        text (str): The legal document text to be split into articles.

    Returns:
        list: A list of dictionaries containing 'article_number' and 'article_text' keys.
              Returns single item with None article_number if no articles found.
    """
    article_pattern = re.compile(r"Article\s+(?:\d{1,2}(?!-)(?:er)?|unique)\b")
    article_match = article_pattern.search(text)

    if not text:
        logger.debug("Empty text provided for cutting.")
        return [{"article_number": None, "article_text": ""}]
    if article_match is None:
        return [{"article_number": None, "article_text": text.strip()}]
    else:
        all_articles = []

        # Finding all articles in the text
        matches = []
        for m in article_pattern.finditer(text):
            matches.append(m)
        if matches:
            for match in matches:
                start = match.start()
                num_match = re.search(r"\d+", match.group())
                if num_match is not None:
                    article_num = int(num_match.group())
                    # Finding the end of the article (next article with a strictly higher number)
                    next_start = len(text)
                    for next_match in matches:
                        next_num_match = re.search(r"\d+", next_match.group())
                        if next_num_match and int(next_num_match.group()) > article_num:
                            next_start = next_match.start()
                            break
                    article_text = text[start:next_start].strip()
                    all_articles.append(
                        {
                            "article_number": article_num,
                            "article_text": article_text.replace("\n\n", "\n"),
                        }
                    )
            return all_articles
        else:
            logger.debug("No articles found in the text.")
            return []


def _dole_cut_exp_memo(text: str, section: str) -> str:
    """
    Extract and parse legal document sections (introduction or articles) from French legal text.

    Args:
        text (str): The legal document text to parse
        section (str): Section type to extract - "introduction" or "articles"

    Returns:
        str: Introduction text if section="introduction"
        list: List of article dictionaries if section="articles", each containing
              article_number, article_synthesis, and title_content
        str: Empty string if text is empty or no content found
    """

    def _get_number_of_articles(text: str) -> int:
        # Finding all occurrences of "Article n" or "Article n-er"
        pattern = re.compile(r"(?:L['’] ?)?[Aa]rticle\s+(?:\d{1,2})(?!-)(?:er)?\b")
        number_pattern = re.compile(r"\d{1,2}")
        matches = pattern.findall(text)
        if matches:
            try:
                # Extract numbers from matches
                # Using regex to find numbers in the matches
                numbers = [
                    int(number_pattern.search(m).group())
                    for m in matches
                    if number_pattern.search(m)
                ]
                # Return the maximum
                return max(numbers)
            except ValueError:
                # If conversion fails, return 0
                logger.debug("Error converting article numbers to integers.")
                return 0
        else:
            return 0

    if not text:
        logger.debug("Empty text provided for cutting.")
        return ""
    article_pattern = re.compile(
        r"(?:L['’] ?a|(?<!')A|l['’]a)rticle\s+\d{1,2}(?!-)(?:er)?$\b"
    )
    title_pattern = re.compile(r"(TITRE\s+\w+)")

    # Finding the first occurrence of TITRE or Article
    titre_match = title_pattern.search(text)
    article_match = article_pattern.search(text)

    # Determine the introduction and the rest of the text
    if titre_match:
        intro = text[: titre_match.start()].strip()
        rest = text[titre_match.start() :].strip()
    elif article_match and not titre_match:
        intro = text[: article_match.start()].strip()
        rest = text[article_match.start() :].strip()
    else:
        intro = None
        rest = text.strip()

    if section.lower() == "introduction":
        return intro

    elif section.lower() == "articles":
        number_of_articles = _get_number_of_articles(text=text)
        all_articles = []

        # Finding all articles and titles in the rest of the text
        matches = []
        for m in article_pattern.finditer(rest):
            matches.append(("article", m))
        for m in title_pattern.finditer(rest):
            matches.append(("title", m))

        if matches:
            # Sort matches by their start position
            matches.sort(key=lambda x: x[1].start())

            articles_texts = {}
            title_ranges = []

            # Preparing title ranges
            for idx, (kind, match) in enumerate(matches):
                if kind == "title":
                    title_start = match.start()
                    # Finding the end of the title
                    title_end = len(rest)
                    for next_idx in range(idx + 1, len(matches)):
                        if matches[next_idx][0] in ("title", "article"):
                            title_end = matches[next_idx][1].start()
                            break
                    title_content = rest[title_start:title_end].strip()
                    title_ranges.append(
                        {
                            "title_start": title_start,
                            "title_end": title_end,
                            "title_content": title_content,
                        }
                    )

            # Associating articles with their titles
            for idx, (kind, match) in enumerate(matches):
                if kind != "article":
                    continue
                start = match.start()
                num_match = re.search(r"\d+", match.group())
                if num_match is not None:
                    article_num = int(num_match.group())
                    # Finding the end of the article (next article with a strictly higher number)
                    next_start = len(rest)
                    for next_idx in range(idx + 1, len(matches)):
                        next_kind, next_match = matches[next_idx]
                        if next_kind == "article":
                            next_num_match = re.search(r"\d+", next_match.group())
                            if (
                                next_num_match
                                and int(next_num_match.group()) > article_num
                            ):
                                next_start = next_match.start()
                                break
                        elif next_kind == "title":
                            next_start = next_match.start()
                            break
                    article_text = rest[start:next_start].strip()

                    # Finding the title content for this article
                    title_content = None

                    for idx, title in enumerate(title_ranges):
                        t_start = title["title_start"]
                        t_end = (
                            title_ranges[idx + 1]["title_start"]
                            if idx + 1 < len(title_ranges)
                            else len(rest)
                        )
                        t_content = title["title_content"]
                        if t_start < start < t_end:
                            title_content = t_content
                            break

                    articles_texts[article_num] = {
                        "article_number": article_num,
                        "article_synthesis": article_text,
                        "title_content": title_content,
                    }

            for number in range(1, number_of_articles + 1):
                article_dict = articles_texts.get(
                    number,
                    {
                        "article_number": number,
                        "article_synthesis": "",
                        "title_content": "",
                    },
                )
                all_articles.append(article_dict)

            return all_articles
        else:
            logger.debug("No articles found in the text.")
            return []
