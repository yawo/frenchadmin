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
import math
import time

try:
    from falkordb import FalkorDB as _FalkorDB

    _FALKORDB_PKG = True
except ImportError:
    _FALKORDB_PKG = False

from config import (
    ENABLE_BATCH_GRAPH_UPSERT,
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


def _sanitize_graph_value(value):
    """Recursively sanitize values before sending them to FalkorDB."""
    if isinstance(value, float):
        return value if math.isfinite(value) else 0.0
    if isinstance(value, dict):
        return {k: _sanitize_graph_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_sanitize_graph_value(v) for v in value]
    return value


def _safe_query(query: str, params: dict):
    """Execute parameterised Cypher query with non-fatal error reporting."""
    graph = _get_graph()
    if graph is None:
        return
    safe_params = _sanitize_graph_value(params or {})
    try:
        graph.query(query, safe_params)
    except Exception as exc:
        doc_ref = ""
        if isinstance(params, dict):
            doc_ref = (
                params.get("doc_id")
                or params.get("source_id")
                or params.get("target_id")
                or ""
            )
        logger.warning(
            "FalkorDB query failed (non-fatal) doc=%s: %s – query: %.120s",
            doc_ref,
            exc,
            " ".join(query.split()),
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
        started_at = time.perf_counter()
        docs = _group_rows_by_doc_id(data_to_insert)
        for doc_id, rows in docs.items():
            first = rows[0]
            chunk_ids = [row[0] for row in rows if row[0]]
            chunk_indexes = [row[2] for row in rows if row[2] is not None]
            chunk_texts = [row[17] or "" for row in rows]
            full_text = "\n".join(text for text in chunk_texts if text)
            embeddings = [row[18] for row in rows if row[18] is not None]

            # Collect references once and pass as parameter array.
            target_doc_ids = set()
            for row in rows:
                try:
                    links = json.loads(row[15]) if row[15] else []
                except (json.JSONDecodeError, TypeError):
                    links = []
                for link in links:
                    target_doc_id = link.get("doc_id") or link.get("text_doc_id")
                    if target_doc_id and target_doc_id not in target_doc_ids:
                        target_doc_ids.add(target_doc_id)

            # Single document query: node upsert + optional relations + references.
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
                "category": first[5] or "",
                "ministry": first[6] or "",
                "target_doc_ids": sorted(target_doc_ids),
            }
            if ENABLE_BATCH_GRAPH_UPSERT:
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

                    FOREACH (_ IN CASE WHEN $category <> '' THEN [1] ELSE [] END |
                        MERGE (code:LegalCode {name: $category})
                        MERGE (d)-[:BELONGS_TO_CODE]->(code)
                    )

                    FOREACH (_ IN CASE WHEN $ministry <> '' THEN [1] ELSE [] END |
                        MERGE (m:Ministry {name: $ministry})
                        MERGE (d)-[:ISSUED_BY]->(m)
                    )

                    FOREACH (target_id IN $target_doc_ids |
                        MERGE (target:LegalText {doc_id: target_id})
                        MERGE (d)-[:REFERENCES]->(target)
                    )
                    """,
                    params,
                )
            else:
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
                if params["category"]:
                    _safe_query(
                        """
                        MERGE (code:LegalCode {name: $name})
                        WITH code
                        MATCH (d:LegalText {doc_id: $doc_id})
                        MERGE (d)-[:BELONGS_TO_CODE]->(code)
                        """,
                        {"name": params["category"], "doc_id": doc_id},
                    )
                if params["ministry"]:
                    _safe_query(
                        """
                        MERGE (m:Ministry {name: $name})
                        WITH m
                        MATCH (d:LegalText {doc_id: $doc_id})
                        MERGE (d)-[:ISSUED_BY]->(m)
                        """,
                        {"name": params["ministry"], "doc_id": doc_id},
                    )
                for target_id in params["target_doc_ids"]:
                    _safe_query(
                        """
                        MERGE (target:LegalText {doc_id: $target_id})
                        WITH target
                        MATCH (source:LegalText {doc_id: $source_id})
                        MERGE (source)-[:REFERENCES]->(target)
                        """,
                        {"target_id": target_id, "source_id": doc_id},
                    )
        elapsed = time.perf_counter() - started_at
        logger.info(
            "Graph upsert stage completed: label=%s docs=%s rows=%s mode=%s sec=%.3f",
            "LegalText",
            len(docs),
            len(data_to_insert),
            "batch" if ENABLE_BATCH_GRAPH_UPSERT else "sequential",
            elapsed,
        )

    except Exception as exc:
        logger.error(
            "Graph upsert stage failed: label=%s rows=%s error=%s",
            "LegalText",
            len(data_to_insert),
            exc,
        )


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
        started_at = time.perf_counter()
        docs = _group_rows_by_doc_id(data_to_insert)
        for doc_id, rows in docs.items():
            first = rows[0]
            chunk_ids = [row[0] for row in rows if row[0]]
            chunk_indexes = [row[2] for row in rows if row[2] is not None]
            chunk_texts = [row[12] or "" for row in rows]
            full_text = "\n".join(text for text in chunk_texts if text)
            embeddings = [row[13] for row in rows if row[13] is not None]

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
                "jurisdiction_value": first[9] or "",
            }
            if ENABLE_BATCH_GRAPH_UPSERT:
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

                    FOREACH (_ IN CASE WHEN $jurisdiction_value <> '' THEN [1] ELSE [] END |
                        MERGE (court:Court {name: $jurisdiction_value})
                        MERGE (d)-[:DECIDED_BY]->(court)
                    )
                    """,
                    params,
                )
            else:
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
                if params["jurisdiction_value"]:
                    _safe_query(
                        """
                        MERGE (court:Court {name: $name})
                        WITH court
                        MATCH (d:JudicialDecision {doc_id: $doc_id})
                        MERGE (d)-[:DECIDED_BY]->(court)
                        """,
                        {"name": params["jurisdiction_value"], "doc_id": doc_id},
                    )
        elapsed = time.perf_counter() - started_at
        logger.info(
            "Graph upsert stage completed: label=%s docs=%s rows=%s mode=%s sec=%.3f",
            "JudicialDecision",
            len(docs),
            len(data_to_insert),
            "batch" if ENABLE_BATCH_GRAPH_UPSERT else "sequential",
            elapsed,
        )

    except Exception as exc:
        logger.error(
            "Graph upsert stage failed: label=%s rows=%s error=%s",
            "JudicialDecision",
            len(data_to_insert),
            exc,
        )


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
        started_at = time.perf_counter()
        docs = _group_rows_by_doc_id(data_to_insert)
        for doc_id, rows in docs.items():
            first = rows[0]
            subjects = first[10]
            subjects_str = "_".join(subjects) if subjects else ""
            chunk_ids = [row[0] for row in rows if row[0]]
            chunk_indexes = [row[2] for row in rows if row[2] is not None]
            chunk_texts = [row[14] or "" for row in rows]
            full_text = "\n".join(text for text in chunk_texts if text)
            embeddings = [row[15] for row in rows if row[15] is not None]

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
                    if target_id and target_id not in target_ids:
                        target_ids.add(target_id)

            params = {
                "doc_id": doc_id,
                "title": first[4] or "",
                "contenu_type": first[6] or "",
                "document_number": first[7] or "",
                "bofip_url": first[8] or "",
                "date": first[9] or "",
                "subjects": subjects_str,
                "category": subjects_str or "",
                "chunk_ids": chunk_ids,
                "chunk_indexes": chunk_indexes,
                "chunk_count": len(rows),
                "chunk_texts": chunk_texts,
                "full_text": full_text,
                "embeddings": embeddings,
                "category_path": first[11] or "",
                "target_ids": sorted(target_ids),
            }
            if ENABLE_BATCH_GRAPH_UPSERT:
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

                    FOREACH (_ IN CASE WHEN $category <> '' THEN [1] ELSE [] END |
                        MERGE (code:TaxCode {name: $category})
                        MERGE (d)-[:BELONGS_TO_CODE]->(code)
                    )

                    FOREACH (target_id IN $target_ids |
                        MERGE (target:TaxGuidance {doc_id: target_id})
                        MERGE (d)-[:REFERENCES]->(target)
                    )
                    """,
                    params,
                )
            else:
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
                if params["category"]:
                    _safe_query(
                        """
                        MERGE (code:TaxCode {name: $name})
                        WITH code
                        MATCH (d:TaxGuidance {doc_id: $doc_id})
                        MERGE (d)-[:BELONGS_TO_CODE]->(code)
                        """,
                        {"name": params["category"], "doc_id": doc_id},
                    )
                for target_id in params["target_ids"]:
                    _safe_query(
                        """
                        MERGE (target:TaxGuidance {doc_id: $target_id})
                        WITH target
                        MATCH (source:TaxGuidance {doc_id: $source_id})
                        MERGE (source)-[:REFERENCES]->(target)
                        """,
                        {"target_id": target_id, "source_id": doc_id},
                    )
        elapsed = time.perf_counter() - started_at
        logger.info(
            "Graph upsert stage completed: label=%s docs=%s rows=%s mode=%s sec=%.3f",
            "TaxGuidance",
            len(docs),
            len(data_to_insert),
            "batch" if ENABLE_BATCH_GRAPH_UPSERT else "sequential",
            elapsed,
        )

    except Exception as exc:
        logger.error(
            "Graph upsert stage failed: label=%s rows=%s error=%s",
            "TaxGuidance",
            len(data_to_insert),
            exc,
        )


# ── Cross-reference graph injection ──────────────────────────────────────────


def inject_cross_reference_edges(source_type: str, source_doc_id: str) -> bool:
    """Inject APPLIES_TO / INTERPRETS edges from cross_reference_legi_edges.

    Only writes from aggregated edge table, never from raw mentions.
    """
    from database.database_manage import get_connection

    if source_type not in {"jade", "bofip"}:
        logger.error(
            "Cross-ref injection received invalid source_type=%s for doc=%s",
            source_type,
            source_doc_id,
        )
        return False

    graph = _get_graph()
    if graph is None:
        return False

    edge_label = "APPLIES_TO" if source_type == "jade" else "INTERPRETS"
    source_label = "JudicialDecision" if source_type == "jade" else "TaxGuidance"

    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                source_doc_id,
                target_legi_doc_id,
                best_confidence,
                occurrence_count,
                resolver_methods,
                normalized_numbers
            FROM cross_reference_legi_edges
            WHERE source_type = %s AND source_doc_id = %s
        """, (source_type, source_doc_id))
        rows = cursor.fetchall()

    if not rows:
        try:
            graph.query(
                f"""
                MATCH (s:{source_label} {{doc_id: $source_doc_id}})-[r:{edge_label}]->(:LegalText)
                DELETE r
                """,
                {"source_doc_id": source_doc_id},
            )
            return True
        except Exception as exc:
            logger.warning(
                f"Cross-ref stale-edge cleanup failed for {source_type}:{source_doc_id}: {exc}"
            )
            return False

    injected = 0
    missing = 0
    failed = 0
    desired_target_ids = sorted({row[1] for row in rows if row[1]})
    upserted_target_ids = set()

    for row in rows:
        src_doc, target_id, conf, occ, methods, norm_nums = row
        params = {
            "source_doc_id": src_doc,
            "target_legi_doc_id": target_id,
            "best_confidence": conf,
            "occurrence_count": occ,
            "resolver_methods": methods or [],
            "normalized_numbers": [n for n in (norm_nums or []) if n],
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        }

        # Verify nodes exist before edge creation
        try:
            check = graph.query(
                f"""
                MATCH (s:{source_label} {{doc_id: $source_doc_id}})
                MATCH (t:LegalText {{doc_id: $target_legi_doc_id}})
                RETURN count(s) as src_count, count(t) as tgt_count
                """,
                params,
            )
            result_set = check.result_set
            if not result_set or result_set[0][0] == 0 or result_set[0][1] == 0:
                missing += 1
                logger.warning(
                    f"Cross-ref edge skipped: source or target node missing "
                    f"({source_label}:{src_doc} → LegalText:{target_id})"
                )
                continue
        except Exception as exc:
            missing += 1
            logger.warning(
                f"FalkorDB node check failed (non-fatal), skipping edge "
                f"({source_label}:{src_doc} → LegalText:{target_id}): {exc}"
            )
            continue

        try:
            graph.query(
                f"""
                MATCH (s:{source_label} {{doc_id: $source_doc_id}})
                MATCH (t:LegalText {{doc_id: $target_legi_doc_id}})
                MERGE (s)-[r:{edge_label}]->(t)
                SET r.confidence = $best_confidence,
                    r.occurrence_count = $occurrence_count,
                    r.resolver_methods = $resolver_methods,
                    r.normalized_numbers = $normalized_numbers,
                    r.updated_at = $updated_at
                """,
                params,
            )
            injected += 1
            upserted_target_ids.add(target_id)
        except Exception as exc:
            failed += 1
            logger.warning(
                f"Cross-ref edge upsert failed (non-fatal) "
                f"({source_label}:{src_doc} → LegalText:{target_id}): {exc}"
            )

    sync_ok = (
        missing == 0
        and failed == 0
    )
    
    # Verify all desired edges were upserted (may fail silently if graph unavailable)
    if len(upserted_target_ids) != len(desired_target_ids):
        missing_targets = len(desired_target_ids) - len(upserted_target_ids)
        logger.warning(
            f"Cross-ref incomplete upsert for {source_type}:{source_doc_id}: "
            f"{missing_targets} targets not reached"
        )
        sync_ok = False

    try:
        graph.query(
            f"""
            MATCH (s:{source_label} {{doc_id: $source_doc_id}})-[r:{edge_label}]->(t:LegalText)
            WHERE NOT t.doc_id IN $desired_target_ids
            DELETE r
            """,
            {
                "source_doc_id": source_doc_id,
                "desired_target_ids": desired_target_ids,
            },
        )
    except Exception as exc:
        sync_ok = False
        logger.warning(
            f"Cross-ref stale-edge prune failed for {source_type}:{source_doc_id}: {exc}"
        )

    if not sync_ok:
        logger.warning(
            f"Cross-ref sync incomplete for {source_type}:{source_doc_id} "
            f"(missing={missing}, failed={failed})"
        )

    logger.info(
        f"Injected {injected}/{len(desired_target_ids)} {edge_label} edges for {source_type}:{source_doc_id} "
        f"(missing={missing}, failed={failed}, sync_ok={sync_ok})"
    )
    return sync_ok
