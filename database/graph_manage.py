"""FalkorDB knowledge graph layer for MEDIATECH.

This module provides a graph layer on top of the existing PostgreSQL pipeline.
All graph operations are **best-effort**: if FalkorDB is unavailable or
misconfigured, every function returns silently so the main ETL pipeline is never
interrupted.

Graph ontology
--------------
Node labels
~~~~~~~~~~~
* ``LegalText``              — LEGI article (doc-level)
* ``LegalTextChunk``         — LEGI article chunk (carries embedding)
* ``JudicialDecision``       — JADE judicial decision (doc-level)
* ``JudicialDecisionChunk``  — JADE chunk (carries embedding)
* ``TaxGuidance``            — BOFiP tax-guidance document (doc-level)
* ``TaxGuidanceChunk``       — BOFiP chunk (carries embedding)
* ``LegalCode``              — Auxiliary: legal code / category (LEGI)
* ``Ministry``               — Auxiliary: issuing ministry (LEGI)
* ``Court``                  — Auxiliary: deciding court / jurisdiction (JADE)
* ``TaxCode``                — Auxiliary: BOFiP taxonomy path

Relationships
~~~~~~~~~~~~~
* ``IS_CHUNK_OF``     — Chunk → Doc (all types)
* ``BELONGS_TO_CODE`` — Doc → LegalCode / TaxCode (LEGI, BOFiP)
* ``ISSUED_BY``       — LegalText → Ministry
* ``DECIDED_BY``      — JudicialDecision → Court
* ``REFERENCES``      — Doc → Doc (LEGI via LIENS, BOFiP via dc:relation)
"""

import json

try:
    from falkordb import FalkorDB as _FalkorDB

    _FALKORDB_PKG = True
except ImportError:
    _FALKORDB_PKG = False

from config import (
    FALKORDB_GRAPH_NAME,
    FALKORDB_HOST,
    FALKORDB_PASSWORD,
    FALKORDB_PORT,
    get_logger,
)

logger = get_logger(__name__)

# Module-level state – all accesses are serialised within a single process
_client = None
_graph = None
_graph_available = None  # None = not yet attempted; True / False = result


def _get_graph():
    """Return the FalkorDB ``Graph`` object, or ``None`` if unavailable.

    Performs lazy initialisation on the first call and caches the result so
    that subsequent calls pay only a dict-lookup cost.
    """
    global _client, _graph, _graph_available

    if _graph_available is False:
        return None

    if _graph is not None:
        return _graph

    if not _FALKORDB_PKG:
        _graph_available = False
        logger.warning(
            "falkordb package not installed – graph layer disabled. "
            "Install it with: pip install falkordb"
        )
        return None

    try:
        _client = _FalkorDB(
            host=FALKORDB_HOST,
            port=FALKORDB_PORT,
            password=FALKORDB_PASSWORD if FALKORDB_PASSWORD else None,
        )
        _graph = _client.select_graph(FALKORDB_GRAPH_NAME)
        _graph_available = True
        logger.info(
            "FalkorDB connected – graph=%s host=%s:%s",
            FALKORDB_GRAPH_NAME,
            FALKORDB_HOST,
            FALKORDB_PORT,
        )
        return _graph
    except Exception as exc:
        _graph_available = False
        logger.warning("FalkorDB unavailable (graph layer disabled): %s", exc)
        return None


def _safe_query(query: str, params: dict):
    """Execute a parameterised Cypher query; swallow all errors silently."""
    graph = _get_graph()
    if graph is None:
        return
    try:
        graph.query(query, params)
    except Exception as exc:
        logger.debug(
            "FalkorDB query error (non-fatal): %s – query: %.120s", exc, query
        )


def _upsert_chunk_embedding(chunk_label: str, chunk_id: str, embedding):
    """Set the embedding property on a chunk node only when a value is present.

    This is called as a follow-up after the main chunk MERGE so that the
    property is omitted entirely when *embedding* is ``None``, avoiding a
    semantically incorrect empty-list placeholder.

    Args:
        chunk_label: Node label (e.g. ``"LegalTextChunk"``).
        chunk_id: Primary key of the chunk node.
        embedding: Embedding vector (list of floats) or ``None``.
    """
    if embedding is None:
        return
    _safe_query(
        f"MATCH (c:{chunk_label} {{chunk_id: $chunk_id}}) SET c.embedding = $embedding",
        {"chunk_id": chunk_id, "embedding": embedding},
    )


# ── Schema initialisation ────────────────────────────────────────────────────


def init_graph_schema():
    """Create property indexes for all node labels.

    Safe to call multiple times; errors from already-existing indexes are
    silently ignored.  Call this once at application startup (e.g. after
    ``create_all_tables``).
    """
    graph = _get_graph()
    if graph is None:
        return

    index_specs = [
        ("LegalText", "doc_id"),
        ("LegalTextChunk", "chunk_id"),
        ("JudicialDecision", "doc_id"),
        ("JudicialDecisionChunk", "chunk_id"),
        ("TaxGuidance", "doc_id"),
        ("TaxGuidanceChunk", "chunk_id"),
        ("LegalCode", "name"),
        ("Ministry", "name"),
        ("Court", "name"),
        ("TaxCode", "name"),
    ]

    for label, prop in index_specs:
        try:
            graph.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
        except Exception:
            pass  # Index already exists – ignore

    logger.info("FalkorDB graph schema / indexes ensured.")


# ── LEGI ─────────────────────────────────────────────────────────────────────


def upsert_legi_node(data_to_insert: list):
    """Upsert a ``LegalText`` doc node and its ``LegalTextChunk`` nodes.

    Creates (or updates) the following graph elements from a batch of chunk
    tuples produced by the LEGI processing pipeline:

    * One ``LegalText`` node per document.
    * One ``LegalTextChunk`` node per chunk with an ``IS_CHUNK_OF``
      relationship pointing to the parent ``LegalText``.
    * A ``LegalCode`` node + ``BELONGS_TO_CODE`` edge when *category* is set.
    * A ``Ministry`` node + ``ISSUED_BY`` edge when *ministry* is set.
    * ``REFERENCES`` edges to other ``LegalText`` nodes derived from the
      ``LIENS`` section already stored in *links_json*.

    Tuple layout (column indices):

    .. code-block::

        0  chunk_id        1  doc_id          2  chunk_index
        3  chunk_xxh64     4  nature          5  category
        6  ministry        7  status          8  title
        9  full_title     10  subtitles      11  number
       12  start_date     13  end_date       14  nota
       15  links_json     16  text           17  chunk_text
       18  embeddings

    Args:
        data_to_insert: List of tuples, one per chunk.  Must not be empty.
    """
    if not data_to_insert:
        return

    graph = _get_graph()
    if graph is None:
        return

    try:
        first = data_to_insert[0]
        doc_id = first[1]

        # 1. Upsert LegalText doc node
        _safe_query(
            """
            MERGE (d:LegalText {doc_id: $doc_id})
            SET d.title        = $title,
                d.full_title   = $full_title,
                d.nature       = $nature,
                d.category     = $category,
                d.ministry     = $ministry,
                d.status       = $status,
                d.number       = $number,
                d.start_date   = $start_date,
                d.end_date     = $end_date
            """,
            {
                "doc_id": doc_id,
                "title": first[8] or "",
                "full_title": first[9] or "",
                "nature": first[4] or "",
                "category": first[5] or "",
                "ministry": first[6] or "",
                "status": first[7] or "",
                "number": first[11] or "",
                "start_date": first[12] or "",
                "end_date": first[13] or "",
            },
        )

        # 2. Upsert each chunk node + IS_CHUNK_OF relationship
        for row in data_to_insert:
            chunk_id = row[0]
            _safe_query(
                """
                MERGE (c:LegalTextChunk {chunk_id: $chunk_id})
                SET c.doc_id      = $doc_id,
                    c.chunk_index = $chunk_index,
                    c.chunk_text  = $chunk_text
                WITH c
                MATCH (d:LegalText {doc_id: $doc_id})
                MERGE (c)-[:IS_CHUNK_OF]->(d)
                """,
                {
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "chunk_index": row[2],
                    "chunk_text": row[17] or "",
                },
            )
            _upsert_chunk_embedding("LegalTextChunk", chunk_id, row[18])

        # 3. BELONGS_TO_CODE
        category = first[5]
        if category:
            _safe_query(
                """
                MERGE (code:LegalCode {name: $name})
                WITH code
                MATCH (d:LegalText {doc_id: $doc_id})
                MERGE (d)-[:BELONGS_TO_CODE]->(code)
                """,
                {"name": category, "doc_id": doc_id},
            )

        # 4. ISSUED_BY
        ministry = first[6]
        if ministry:
            _safe_query(
                """
                MERGE (m:Ministry {name: $name})
                WITH m
                MATCH (d:LegalText {doc_id: $doc_id})
                MERGE (d)-[:ISSUED_BY]->(m)
                """,
                {"name": ministry, "doc_id": doc_id},
            )

        # 5. REFERENCES (from LIENS already stored in links_json)
        try:
            links = json.loads(first[15]) if first[15] else []
        except (json.JSONDecodeError, TypeError):
            links = []

        for link in links:
            # Prefer the article-level doc_id, fall back to text-level id
            target_doc_id = link.get("doc_id") or link.get("text_doc_id")
            if not target_doc_id:
                continue
            _safe_query(
                """
                MERGE (target:LegalText {doc_id: $target_id})
                WITH target
                MATCH (source:LegalText {doc_id: $source_id})
                MERGE (source)-[:REFERENCES]->(target)
                """,
                {"target_id": target_doc_id, "source_id": doc_id},
            )

    except Exception as exc:
        logger.warning("upsert_legi_node failed (non-fatal): %s", exc)


# ── JADE ─────────────────────────────────────────────────────────────────────


def upsert_jade_node(data_to_insert: list):
    """Upsert a ``JudicialDecision`` doc node and its chunk nodes.

    Creates (or updates):

    * One ``JudicialDecision`` node per document.
    * One ``JudicialDecisionChunk`` node per chunk with ``IS_CHUNK_OF``.
    * A ``Court`` node + ``DECIDED_BY`` edge when *jurisdiction* is set.

    Tuple layout:

    .. code-block::

        0  chunk_id       1  doc_id          2  chunk_index
        3  chunk_xxh64    4  nature          5  solution
        6  title          7  number          8  decision_date
        9  jurisdiction  10  formation      11  text
       12  chunk_text    13  embeddings

    Args:
        data_to_insert: List of tuples, one per chunk.
    """
    if not data_to_insert:
        return

    graph = _get_graph()
    if graph is None:
        return

    try:
        first = data_to_insert[0]
        doc_id = first[1]

        # 1. Upsert JudicialDecision doc node
        _safe_query(
            """
            MERGE (d:JudicialDecision {doc_id: $doc_id})
            SET d.nature        = $nature,
                d.solution      = $solution,
                d.title         = $title,
                d.number        = $number,
                d.decision_date = $decision_date,
                d.jurisdiction  = $jurisdiction,
                d.formation     = $formation
            """,
            {
                "doc_id": doc_id,
                "nature": first[4] or "",
                "solution": first[5] or "",
                "title": first[6] or "",
                "number": first[7] or "",
                "decision_date": first[8] or "",
                "jurisdiction": first[9] or "",
                "formation": first[10] or "",
            },
        )

        # 2. Upsert each chunk node + IS_CHUNK_OF relationship
        for row in data_to_insert:
            chunk_id = row[0]
            _safe_query(
                """
                MERGE (c:JudicialDecisionChunk {chunk_id: $chunk_id})
                SET c.doc_id      = $doc_id,
                    c.chunk_index = $chunk_index,
                    c.chunk_text  = $chunk_text
                WITH c
                MATCH (d:JudicialDecision {doc_id: $doc_id})
                MERGE (c)-[:IS_CHUNK_OF]->(d)
                """,
                {
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "chunk_index": row[2],
                    "chunk_text": row[12] or "",
                },
            )
            _upsert_chunk_embedding("JudicialDecisionChunk", chunk_id, row[13])

        # 3. DECIDED_BY
        jurisdiction = first[9]
        if jurisdiction:
            _safe_query(
                """
                MERGE (court:Court {name: $name})
                WITH court
                MATCH (d:JudicialDecision {doc_id: $doc_id})
                MERGE (d)-[:DECIDED_BY]->(court)
                """,
                {"name": jurisdiction, "doc_id": doc_id},
            )

    except Exception as exc:
        logger.warning("upsert_jade_node failed (non-fatal): %s", exc)


# ── BOFiP ────────────────────────────────────────────────────────────────────


def upsert_bofip_node(data_to_insert: list):
    """Upsert a ``TaxGuidance`` doc node and its chunk nodes.

    Creates (or updates):

    * One ``TaxGuidance`` node per document.
    * One ``TaxGuidanceChunk`` node per chunk with ``IS_CHUNK_OF``.
    * A ``TaxCode`` node + ``BELONGS_TO_CODE`` edge using *category_path*.
    * ``REFERENCES`` edges derived from ``dc:relation`` links of type
      ``"references"`` already stored in *links_json*.

    Tuple layout:

    .. code-block::

        0  chunk_id           1  doc_id             2  chunk_index
        3  chunk_xxh64        4  title              5  contenu_id
        6  contenu_type       7  document_number    8  bofip_url
        9  publication_date  10  subjects (list)   11  category_path
       12  links_json        13  text              14  chunk_text
       15  embeddings

    The ``category`` property on the ``TaxGuidance`` node is set from
    *category_path* (existing BOFIP field) as required by the graph ontology.

    Args:
        data_to_insert: List of tuples, one per chunk (usually a single tuple
            for BOFiP since each document is processed as one chunk).
    """
    if not data_to_insert:
        return

    graph = _get_graph()
    if graph is None:
        return

    try:
        first = data_to_insert[0]
        doc_id = first[1]
        subjects = first[10]
        subjects_str = ", ".join(subjects) if subjects else ""

        # 1. Upsert TaxGuidance doc node
        _safe_query(
            """
            MERGE (d:TaxGuidance {doc_id: $doc_id})
            SET d.title            = $title,
                d.contenu_type     = $contenu_type,
                d.document_number  = $document_number,
                d.bofip_url        = $bofip_url,
                d.date             = $date,
                d.subjects         = $subjects,
                d.category         = $category
            """,
            {
                "doc_id": doc_id,
                "title": first[4] or "",
                "contenu_type": first[6] or "",
                "document_number": first[7] or "",
                "bofip_url": first[8] or "",
                "date": first[9] or "",
                "subjects": subjects_str,
                # category_path is the existing BOFIP taxonomy field
                "category": first[11] or "",
            },
        )

        # 2. Upsert each chunk node + IS_CHUNK_OF relationship
        for row in data_to_insert:
            chunk_id = row[0]
            _safe_query(
                """
                MERGE (c:TaxGuidanceChunk {chunk_id: $chunk_id})
                SET c.doc_id      = $doc_id,
                    c.chunk_index = $chunk_index,
                    c.chunk_text  = $chunk_text
                WITH c
                MATCH (d:TaxGuidance {doc_id: $doc_id})
                MERGE (c)-[:IS_CHUNK_OF]->(d)
                """,
                {
                    "chunk_id": chunk_id,
                    "doc_id": doc_id,
                    "chunk_index": row[2],
                    "chunk_text": row[14] or "",
                },
            )
            _upsert_chunk_embedding("TaxGuidanceChunk", chunk_id, row[15])

        # 3. BELONGS_TO_CODE (using category_path as the taxonomy identifier)
        category_path = first[11]
        if category_path:
            _safe_query(
                """
                MERGE (code:TaxCode {name: $name})
                WITH code
                MATCH (d:TaxGuidance {doc_id: $doc_id})
                MERGE (d)-[:BELONGS_TO_CODE]->(code)
                """,
                {"name": category_path, "doc_id": doc_id},
            )

        # 4. REFERENCES (dc:relation links of type "references")
        try:
            links = json.loads(first[12]) if first[12] else []
        except (json.JSONDecodeError, TypeError):
            links = []

        for link in links:
            if link.get("type") != "references":
                continue
            target_id = link.get("id")
            if not target_id:
                continue
            _safe_query(
                """
                MERGE (target:TaxGuidance {doc_id: $target_id})
                WITH target
                MATCH (source:TaxGuidance {doc_id: $source_id})
                MERGE (source)-[:REFERENCES]->(target)
                """,
                {"target_id": target_id, "source_id": doc_id},
            )

    except Exception as exc:
        logger.warning("upsert_bofip_node failed (non-fatal): %s", exc)
