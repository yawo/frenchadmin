"""FalkorDB knowledge graph layer for MEDIATECH.

This module provides a graph layer on top of the existing PostgreSQL pipeline.
All graph operations are **best-effort**: if FalkorDB is unavailable or
misconfigured, every function returns silently so the main ETL pipeline is never
interrupted.

Graph ontology
--------------
Node labels
~~~~~~~~~~~
* ``LegalText``       — LEGI article (doc-level; carries all chunk texts + embeddings)
* ``JudicialDecision`` — JADE judicial decision (doc-level; carries all chunk texts + embeddings)
* ``TaxGuidance``     — BOFiP tax-guidance document (doc-level; carries chunk text + embedding)
* ``LegalCode``       — Auxiliary: legal code / category (LEGI)
* ``Ministry``        — Auxiliary: issuing ministry (LEGI)
* ``Court``           — Auxiliary: deciding court / jurisdiction (JADE)
* ``TaxCode``         — Auxiliary: BOFiP taxonomy path

Chunk data (texts and embeddings) are stored as list properties on the
doc-level node itself.  No separate chunk nodes are created.

Relationships
~~~~~~~~~~~~~
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


def _group_rows_by_doc_id(data_to_insert: list) -> dict:
    """Group chunk rows by ``doc_id`` and sort each group by chunk index."""
    grouped: dict = {}
    for row in data_to_insert:
        if not row or len(row) < 3:
            continue
        doc_id = row[1]
        grouped.setdefault(doc_id, []).append(row)

    for doc_id in grouped:
        grouped[doc_id].sort(key=lambda row: row[2] if row[2] is not None else 0)

    return grouped


# ── Schema initialisation ────────────────────────────────────────────────────


def init_graph_schema():
    """Create property indexes for all doc-level node labels.

    Safe to call multiple times; errors from already-existing indexes are
    silently ignored.  Call this once at application startup (e.g. after
    ``create_all_tables``).
    """
    graph = _get_graph()
    if graph is None:
        return

    index_specs = [
        ("LegalText", "doc_id"),
        ("JudicialDecision", "doc_id"),
        ("TaxGuidance", "doc_id"),
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
    """Upsert LEGI document nodes from one or many chunk rows.

    Rows are grouped by doc_id, chunk data is aggregated as list properties,
    and relationships are deduplicated across all chunks.
    """
    if not data_to_insert:
        return

    graph = _get_graph()
    if graph is None:
        return

    try:
        docs = _group_rows_by_doc_id(data_to_insert)
        for doc_id, rows in docs.items():
            first = rows[0]
            chunk_ids = [row[0] for row in rows if row[0]]
            chunk_indexes = [row[2] for row in rows if row[2] is not None]
            chunk_texts = [row[17] or "" for row in rows]
            full_text = "\n".join(text for text in chunk_texts if text)
            embeddings = [row[18] for row in rows if row[18] is not None]

            # 1. Upsert LegalText doc node with aggregated chunk data
            params = {
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
                "chunk_ids": chunk_ids,
                "chunk_indexes": chunk_indexes,
                "chunk_count": len(rows),
                "chunk_texts": chunk_texts,
                "full_text": full_text,
                "embeddings": embeddings,
            }
            _safe_query(
                """
                MERGE (d:LegalText {doc_id: $doc_id})
                SET d.title         = $title,
                    d.full_title    = $full_title,
                    d.nature        = $nature,
                    d.category      = $category,
                    d.ministry      = $ministry,
                    d.status        = $status,
                    d.number        = $number,
                    d.start_date    = $start_date,
                    d.end_date      = $end_date,
                    d.chunk_ids     = $chunk_ids,
                    d.chunk_indexes = $chunk_indexes,
                    d.chunk_count   = $chunk_count,
                    d.chunk_texts   = $chunk_texts,
                    d.full_text     = $full_text,
                    d.embeddings    = $embeddings
                """,
                params,
            )

            # 2. BELONGS_TO_CODE
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

            # 3. ISSUED_BY
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

            # 4. REFERENCES (deduplicated from all chunk rows)
            target_doc_ids = set()
            for row in rows:
                try:
                    links = json.loads(row[15]) if row[15] else []
                except (json.JSONDecodeError, TypeError):
                    links = []
                for link in links:
                    target_doc_id = link.get("doc_id") or link.get("text_doc_id")
                    if target_doc_id:
                        target_doc_ids.add(target_doc_id)

            for target_doc_id in target_doc_ids:
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
    """Upsert one or more ``JudicialDecision`` nodes from JADE chunk rows.

    ``data_to_insert`` may contain many chunk tuples for the same document. The
    function groups rows by ``doc_id`` and stores chunk-level data as lists on
    the doc-level node.

    Properties set on the node:

    * Metadata from the chunk: ``nature``, ``solution``, ``title``,
      ``number``, ``decision_date``, ``jurisdiction``, ``formation``.
    * ``chunk_ids`` / ``chunk_indexes`` / ``chunk_count`` for traceability.
    * ``chunk_texts`` — list of chunk texts in ``chunk_index`` order.
    * ``full_text``   — newline-joined full document text.
    * ``embeddings``  — list of chunk embedding vectors.

    Relationships created:

    * ``DECIDED_BY`` → ``Court`` (when *jurisdiction* is non-empty).

    Tuple layout:

    .. code-block::

        0  chunk_id       1  doc_id          2  chunk_index
        3  chunk_xxh64    4  nature          5  solution
        6  title          7  number          8  decision_date
        9  jurisdiction  10  formation      11  text
       12  chunk_text    13  embeddings

    Args:
        data_to_insert: List of chunk tuples for one or many JADE documents.
    """
    if not data_to_insert:
        return

    graph = _get_graph()
    if graph is None:
        return

    try:
        docs = _group_rows_by_doc_id(data_to_insert)
        for doc_id, rows in docs.items():
            first = rows[0]
            chunk_ids = [row[0] for row in rows if row[0]]
            chunk_indexes = [row[2] for row in rows if row[2] is not None]
            chunk_texts = [row[12] or "" for row in rows]
            full_text = "\n".join(text for text in chunk_texts if text)
            embeddings = [row[13] for row in rows if row[13] is not None]

            # 1. Upsert JudicialDecision doc node with aggregated chunk data
            params = {
                "doc_id": doc_id,
                "nature": first[4] or "",
                "solution": first[5] or "",
                "title": first[6] or "",
                "number": first[7] or "",
                "decision_date": first[8] or "",
                "jurisdiction": first[9] or "",
                "formation": first[10] or "",
                "chunk_ids": chunk_ids,
                "chunk_indexes": chunk_indexes,
                "chunk_count": len(rows),
                "chunk_texts": chunk_texts,
                "full_text": full_text,
                "embeddings": embeddings,
            }
            _safe_query(
                """
                MERGE (d:JudicialDecision {doc_id: $doc_id})
                SET d.nature        = $nature,
                    d.solution      = $solution,
                    d.title         = $title,
                    d.number        = $number,
                    d.decision_date = $decision_date,
                    d.jurisdiction  = $jurisdiction,
                    d.formation     = $formation,
                    d.chunk_ids     = $chunk_ids,
                    d.chunk_indexes = $chunk_indexes,
                    d.chunk_count   = $chunk_count,
                    d.chunk_texts   = $chunk_texts,
                    d.full_text     = $full_text,
                    d.embeddings    = $embeddings
                """,
                params,
            )

            # 2. DECIDED_BY
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
    """Upsert one or more ``TaxGuidance`` nodes from BOFiP chunk rows.

    ``data_to_insert`` may contain many chunk tuples for the same document. The
    function groups rows by ``doc_id`` and stores chunk-level data as lists on
    the doc-level node.

    Properties set on the node:

    * Metadata from the chunk: ``title``, ``contenu_type``,
      ``document_number``, ``bofip_url``, ``date``, ``subjects``,
      ``category`` (from *category_path* — the existing BOFIP taxonomy field).
    * ``chunk_ids`` / ``chunk_indexes`` / ``chunk_count`` for traceability.
    * ``chunk_texts`` — list of chunk texts in ``chunk_index`` order.
    * ``full_text``   — newline-joined full document text.
    * ``embeddings``  — list of chunk embedding vectors.

    Relationships created:

    * ``BELONGS_TO_CODE`` → ``TaxCode`` using *category_path* as the key.
    * ``REFERENCES``      → ``TaxGuidance`` for each ``dc:relation`` entry of
      type ``"references"`` stored in *links_json*.

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
        data_to_insert: List of chunk tuples for one or many BOFiP documents.
    """
    if not data_to_insert:
        return

    graph = _get_graph()
    if graph is None:
        return

    try:
        docs = _group_rows_by_doc_id(data_to_insert)
        for doc_id, rows in docs.items():
            first = rows[0]
            subjects = first[10]
            subjects_str = ", ".join(subjects) if subjects else ""
            chunk_ids = [row[0] for row in rows if row[0]]
            chunk_indexes = [row[2] for row in rows if row[2] is not None]
            chunk_texts = [row[14] or "" for row in rows]
            full_text = "\n".join(text for text in chunk_texts if text)
            embeddings = [row[15] for row in rows if row[15] is not None]

            # 1. Upsert TaxGuidance doc node with aggregated chunk data
            params = {
                "doc_id": doc_id,
                "title": first[4] or "",
                "contenu_type": first[6] or "",
                "document_number": first[7] or "",
                "bofip_url": first[8] or "",
                "date": first[9] or "",
                "subjects": subjects_str,
                # category_path is the existing BOFIP taxonomy field
                "category": first[11] or "",
                "chunk_ids": chunk_ids,
                "chunk_indexes": chunk_indexes,
                "chunk_count": len(rows),
                "chunk_texts": chunk_texts,
                "full_text": full_text,
                "embeddings": embeddings,
            }
            _safe_query(
                """
                MERGE (d:TaxGuidance {doc_id: $doc_id})
                SET d.title            = $title,
                    d.contenu_type     = $contenu_type,
                    d.document_number  = $document_number,
                    d.bofip_url        = $bofip_url,
                    d.date             = $date,
                    d.subjects         = $subjects,
                    d.category         = $category,
                    d.chunk_ids        = $chunk_ids,
                    d.chunk_indexes    = $chunk_indexes,
                    d.chunk_count      = $chunk_count,
                    d.chunk_texts      = $chunk_texts,
                    d.full_text        = $full_text,
                    d.embeddings       = $embeddings
                """,
                params,
            )

            # 2. BELONGS_TO_CODE (using category_path as the taxonomy identifier)
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

            # 3. REFERENCES (dc:relation links of type "references")
            target_ids = set()
            for row in rows:
                try:
                    links = json.loads(row[12]) if row[12] else []
                except (json.JSONDecodeError, TypeError):
                    links = []
                for link in links:
                    if link.get("type") != "references":
                        continue
                    target_id = link.get("id")
                    if target_id:
                        target_ids.add(target_id)

            for target_id in target_ids:
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

