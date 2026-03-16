"""
Knowledge Graph management module for FalkorDB.

This module implements the FrenchAdmin knowledge graph on top of FalkorDB,
a graph database built on Redis. It provides the ontology, population
functions, and GraphRAG integration for LEGI, JADE, and BOFIP data.

Graph Ontology
--------------
Node labels:
  - LegalText       : LEGI articles / legislative texts
  - JudicialDecision: JADE administrative court decisions
  - TaxGuidance     : BOFIP tax administration guidance
  - LegalCode       : French legal codes (Code civil, Code pénal, …)
  - Ministry        : French ministries
  - Jurisdiction    : Administrative courts / jurisdictions
  - Chunk           : Text chunks linked to a source document (GraphRAG)

Relationship types:
  - BELONGS_TO_CODE : (LegalText | TaxGuidance) → LegalCode
  - ISSUED_BY       : LegalText → Ministry
  - REFERENCES      : LegalText → LegalText  (from LIENS metadata)
  - DECIDED_BY      : JudicialDecision → Jurisdiction
  - PART_OF         : Chunk → (LegalText | JudicialDecision | TaxGuidance)

Design decision
---------------
Nodes and relationships are populated **in parallel** with the PostgreSQL
inserts (Option B), i.e. during raw XML/CSV processing rather than from the
already-stored relational data. This avoids a second full-data pass, keeps
both stores consistent, and exposes richer structural information (e.g. the
LIENS cross-references available only in the raw XML).

All graph operations are wrapped in try/except so that a FalkorDB outage or
misconfiguration never interrupts the main PostgreSQL pipeline.
"""

import json

from config import (
    FALKORDB_GRAPH_NAME,
    FALKORDB_HOST,
    FALKORDB_PASSWORD,
    FALKORDB_PORT,
    FALKORDB_USERNAME,
    get_logger,
)

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Lazy FalkorDB connection
# ---------------------------------------------------------------------------

_graph = None


def _get_graph():
    """Return the shared FalkorDB graph handle, creating it on first call.

    Returns ``None`` if FalkorDB is unavailable or not configured, which
    allows callers to skip graph operations gracefully.
    """
    global _graph
    if _graph is not None:
        return _graph
    try:
        import falkordb  # noqa: PLC0415  (imported lazily to keep startup fast)

        kwargs = {"host": FALKORDB_HOST, "port": FALKORDB_PORT}
        if FALKORDB_USERNAME:
            kwargs["username"] = FALKORDB_USERNAME
        if FALKORDB_PASSWORD:
            kwargs["password"] = FALKORDB_PASSWORD

        client = falkordb.FalkorDB(**kwargs)
        _graph = client.select_graph(FALKORDB_GRAPH_NAME)
        logger.info(
            "Connected to FalkorDB at %s:%s, graph '%s'",
            FALKORDB_HOST,
            FALKORDB_PORT,
            FALKORDB_GRAPH_NAME,
        )
        return _graph
    except Exception as exc:
        logger.warning(
            "FalkorDB is unavailable (%s). Knowledge-graph operations will be skipped.",
            exc,
        )
        return None


def close_graph_connection():
    """Reset the cached graph handle (useful for testing)."""
    global _graph
    _graph = None


# ---------------------------------------------------------------------------
# Schema initialisation
# ---------------------------------------------------------------------------

_INDEXES = [
    ("LegalText", "doc_id"),
    ("JudicialDecision", "doc_id"),
    ("TaxGuidance", "doc_id"),
    ("LegalCode", "name"),
    ("Ministry", "name"),
    ("Jurisdiction", "name"),
    ("Chunk", "chunk_id"),
]


def init_graph_schema():
    """Create indexes required for efficient node lookups.

    Safe to call multiple times – existing indexes are silently ignored.
    """
    graph = _get_graph()
    if graph is None:
        return
    for label, prop in _INDEXES:
        try:
            graph.query(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
            logger.debug("Index created: (%s).%s", label, prop)
        except Exception as exc:
            if "already indexed" in str(exc).lower() or "equivalent index" in str(exc).lower():
                logger.debug("Index already exists: (%s).%s", label, prop)
            else:
                logger.warning("Could not create index (%s).%s: %s", label, prop, exc)
    logger.info("FalkorDB graph schema initialised for graph '%s'", FALKORDB_GRAPH_NAME)


# ---------------------------------------------------------------------------
# Helper: fire-and-forget Cypher
# ---------------------------------------------------------------------------

def _run(query: str, params: dict | None = None):
    """Execute a Cypher query, logging any errors without raising."""
    graph = _get_graph()
    if graph is None:
        return
    try:
        graph.query(query, params or {})
    except Exception as exc:
        logger.warning("Graph query failed: %s | params=%s | error=%s", query, params, exc)


# ---------------------------------------------------------------------------
# Node upsert helpers
# ---------------------------------------------------------------------------

def _upsert_legal_code(name: str):
    if not name:
        return
    _run(
        "MERGE (n:LegalCode {name: $name})",
        {"name": name},
    )


def _upsert_ministry(name: str):
    if not name:
        return
    _run(
        "MERGE (n:Ministry {name: $name})",
        {"name": name},
    )


def _upsert_jurisdiction(name: str):
    if not name:
        return
    _run(
        "MERGE (n:Jurisdiction {name: $name})",
        {"name": name},
    )


# ---------------------------------------------------------------------------
# LEGI — LegalText nodes + relationships
# ---------------------------------------------------------------------------

def upsert_legi_node(
    doc_id: str,
    nature: str | None,
    category: str | None,
    ministry: str | None,
    status: str | None,
    title: str | None,
    full_title: str | None,
    number: str | None,
    start_date: str | None,
    end_date: str | None,
    links: list | None = None,
):
    """Insert or update a :LegalText node for a LEGI article.

    Also creates :LegalCode and :Ministry nodes and the corresponding
    BELONGS_TO_CODE, ISSUED_BY, and REFERENCES relationships.

    Args:
        doc_id: Unique document identifier (CID).
        nature: Legal nature of the text (e.g. "LOI", "DECRET").
        category: Legal code the article belongs to.
        ministry: Ministry that issued the text.
        status: Current status (e.g. "VIGUEUR").
        title: Short title.
        full_title: Full title.
        number: Article number.
        start_date: Validity start date (YYYY-MM-DD).
        end_date: Validity end date (YYYY-MM-DD).
        links: List of cross-reference dicts from the LIENS XML element.
    """
    graph = _get_graph()
    if graph is None:
        return

    # Upsert the LegalText node
    _run(
        """
        MERGE (n:LegalText {doc_id: $doc_id})
        SET n.nature     = $nature,
            n.category   = $category,
            n.ministry   = $ministry,
            n.status     = $status,
            n.title      = $title,
            n.full_title = $full_title,
            n.number     = $number,
            n.start_date = $start_date,
            n.end_date   = $end_date
        """,
        {
            "doc_id": doc_id,
            "nature": nature,
            "category": category,
            "ministry": ministry,
            "status": status,
            "title": title,
            "full_title": full_title,
            "number": number,
            "start_date": start_date,
            "end_date": end_date,
        },
    )

    # LegalCode relationship
    if category:
        _upsert_legal_code(category)
        _run(
            """
            MATCH (lt:LegalText {doc_id: $doc_id}), (lc:LegalCode {name: $name})
            MERGE (lt)-[:BELONGS_TO_CODE]->(lc)
            """,
            {"doc_id": doc_id, "name": category},
        )

    # Ministry relationship
    if ministry:
        _upsert_ministry(ministry)
        _run(
            """
            MATCH (lt:LegalText {doc_id: $doc_id}), (m:Ministry {name: $name})
            MERGE (lt)-[:ISSUED_BY]->(m)
            """,
            {"doc_id": doc_id, "name": ministry},
        )

    # Cross-references (LIENS)
    if links:
        for link in links:
            ref_doc_id = link.get("doc_id")
            if not ref_doc_id:
                continue
            # Ensure the referenced node exists (minimal stub)
            _run(
                "MERGE (n:LegalText {doc_id: $ref_id})",
                {"ref_id": ref_doc_id},
            )
            _run(
                """
                MATCH (src:LegalText {doc_id: $src_id}),
                      (dst:LegalText {doc_id: $dst_id})
                MERGE (src)-[:REFERENCES {type: $link_type}]->(dst)
                """,
                {
                    "src_id": doc_id,
                    "dst_id": ref_doc_id,
                    "link_type": link.get("link_type", ""),
                },
            )


def upsert_legi_chunk(chunk_id: str, doc_id: str):
    """Link a text chunk to its parent :LegalText node.

    Args:
        chunk_id: Unique chunk identifier.
        doc_id: Parent document identifier.
    """
    graph = _get_graph()
    if graph is None:
        return
    _run(
        """
        MERGE (c:Chunk {chunk_id: $chunk_id})
        SET c.source_type = 'legi'
        WITH c
        MATCH (lt:LegalText {doc_id: $doc_id})
        MERGE (c)-[:PART_OF]->(lt)
        """,
        {"chunk_id": chunk_id, "doc_id": doc_id},
    )


# ---------------------------------------------------------------------------
# JADE — JudicialDecision nodes + relationships
# ---------------------------------------------------------------------------

def upsert_jade_node(
    doc_id: str,
    nature: str | None,
    solution: str | None,
    title: str | None,
    number: str | None,
    decision_date: str | None,
    jurisdiction: str | None,
    formation: str | None,
):
    """Insert or update a :JudicialDecision node for a JADE decision.

    Also creates a :Jurisdiction node and the DECIDED_BY relationship.

    Args:
        doc_id: Unique document identifier (CID).
        nature: Nature of the decision.
        solution: Decision solution (e.g. "Rejet", "Annulation").
        title: Title of the decision.
        number: Decision number.
        decision_date: Date of the decision (YYYY-MM-DD).
        jurisdiction: Name of the issuing court.
        formation: Court formation that rendered the decision.
    """
    graph = _get_graph()
    if graph is None:
        return

    _run(
        """
        MERGE (n:JudicialDecision {doc_id: $doc_id})
        SET n.nature         = $nature,
            n.solution       = $solution,
            n.title          = $title,
            n.number         = $number,
            n.decision_date  = $decision_date,
            n.jurisdiction   = $jurisdiction,
            n.formation      = $formation
        """,
        {
            "doc_id": doc_id,
            "nature": nature,
            "solution": solution,
            "title": title,
            "number": number,
            "decision_date": decision_date,
            "jurisdiction": jurisdiction,
            "formation": formation,
        },
    )

    # Jurisdiction relationship
    if jurisdiction:
        _upsert_jurisdiction(jurisdiction)
        _run(
            """
            MATCH (jd:JudicialDecision {doc_id: $doc_id}),
                  (j:Jurisdiction {name: $name})
            MERGE (jd)-[:DECIDED_BY]->(j)
            """,
            {"doc_id": doc_id, "name": jurisdiction},
        )


def upsert_jade_chunk(chunk_id: str, doc_id: str):
    """Link a text chunk to its parent :JudicialDecision node.

    Args:
        chunk_id: Unique chunk identifier.
        doc_id: Parent document identifier.
    """
    graph = _get_graph()
    if graph is None:
        return
    _run(
        """
        MERGE (c:Chunk {chunk_id: $chunk_id})
        SET c.source_type = 'jade'
        WITH c
        MATCH (jd:JudicialDecision {doc_id: $doc_id})
        MERGE (c)-[:PART_OF]->(jd)
        """,
        {"chunk_id": chunk_id, "doc_id": doc_id},
    )


# ---------------------------------------------------------------------------
# BOFIP — TaxGuidance nodes + relationships
# ---------------------------------------------------------------------------

def upsert_bofip_node(
    doc_id: str,
    nature: str | None,
    category: str | None,
    title: str | None,
    date: str | None,
):
    """Insert or update a :TaxGuidance node for a BOFIP document.

    Also creates a :LegalCode node and the BELONGS_TO_CODE relationship when
    a category is provided.

    Args:
        doc_id: Unique document identifier (CID).
        nature: Nature of the guidance document.
        category: Tax category / legal code reference.
        title: Title of the guidance document.
        date: Publication date (YYYY-MM-DD).
    """
    graph = _get_graph()
    if graph is None:
        return

    _run(
        """
        MERGE (n:TaxGuidance {doc_id: $doc_id})
        SET n.nature   = $nature,
            n.category = $category,
            n.title    = $title,
            n.date     = $date
        """,
        {
            "doc_id": doc_id,
            "nature": nature,
            "category": category,
            "title": title,
            "date": date,
        },
    )

    if category:
        _upsert_legal_code(category)
        _run(
            """
            MATCH (tg:TaxGuidance {doc_id: $doc_id}),
                  (lc:LegalCode {name: $name})
            MERGE (tg)-[:BELONGS_TO_CODE]->(lc)
            """,
            {"doc_id": doc_id, "name": category},
        )


def upsert_bofip_chunk(chunk_id: str, doc_id: str):
    """Link a text chunk to its parent :TaxGuidance node.

    Args:
        chunk_id: Unique chunk identifier.
        doc_id: Parent document identifier.
    """
    graph = _get_graph()
    if graph is None:
        return
    _run(
        """
        MERGE (c:Chunk {chunk_id: $chunk_id})
        SET c.source_type = 'bofip'
        WITH c
        MATCH (tg:TaxGuidance {doc_id: $doc_id})
        MERGE (c)-[:PART_OF]->(tg)
        """,
        {"chunk_id": chunk_id, "doc_id": doc_id},
    )


# ---------------------------------------------------------------------------
# Bulk population from PostgreSQL (alternative / back-fill path)
# ---------------------------------------------------------------------------

def populate_graph_from_postgres():
    """Populate the knowledge graph from data already stored in PostgreSQL.

    This is the alternative to the parallel-population approach (Option A).
    It is useful for a one-off back-fill when FalkorDB is introduced to an
    existing deployment where PostgreSQL data has already been ingested.

    The function reads from the LEGI, JADE, and (if present) BOFIP tables and
    re-creates the graph nodes and relationships.  Large tables are streamed
    with server-side cursors to avoid loading everything into memory.
    """
    try:
        import psycopg2  # noqa: PLC0415

        from config import (  # noqa: PLC0415
            POSTGRES_DB,
            POSTGRES_HOST,
            POSTGRES_PASSWORD,
            POSTGRES_PORT,
            POSTGRES_USER,
        )

        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
        )
    except Exception as exc:
        logger.error("Cannot connect to PostgreSQL for graph back-fill: %s", exc)
        return

    try:
        _populate_legi_from_postgres(conn)
        _populate_jade_from_postgres(conn)
        _populate_bofip_from_postgres(conn)
    finally:
        conn.close()
    logger.info("Graph back-fill from PostgreSQL completed.")


def _populate_legi_from_postgres(conn):
    """Back-fill :LegalText nodes from the LEGI PostgreSQL table."""
    graph = _get_graph()
    if graph is None:
        return
    try:
        with conn.cursor(name="legi_cursor") as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (doc_id)
                    doc_id, nature, category, ministry, status,
                    title, full_title, number, start_date, end_date, links
                FROM LEGI
                ORDER BY doc_id, chunk_index
                """
            )
            count = 0
            for row in cur:
                (
                    doc_id, nature, category, ministry, status,
                    title, full_title, number, start_date, end_date, links_json,
                ) = row
                links = []
                if links_json:
                    try:
                        links = json.loads(links_json) if isinstance(links_json, str) else links_json
                    except (json.JSONDecodeError, TypeError):
                        links = []
                upsert_legi_node(
                    doc_id=doc_id,
                    nature=nature,
                    category=category,
                    ministry=ministry,
                    status=status,
                    title=title,
                    full_title=full_title,
                    number=number,
                    start_date=start_date,
                    end_date=end_date,
                    links=links,
                )
                count += 1
                if count % 10000 == 0:
                    logger.info("LEGI back-fill: %d nodes upserted", count)
        logger.info("LEGI back-fill complete: %d nodes total", count)
    except Exception as exc:
        logger.error("Error during LEGI graph back-fill: %s", exc)


def _populate_jade_from_postgres(conn):
    """Back-fill :JudicialDecision nodes from the JADE PostgreSQL table."""
    graph = _get_graph()
    if graph is None:
        return
    try:
        with conn.cursor(name="jade_cursor") as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (doc_id)
                    doc_id, nature, solution, title, number,
                    decision_date, jurisdiction, formation
                FROM JADE
                ORDER BY doc_id, chunk_index
                """
            )
            count = 0
            for row in cur:
                (
                    doc_id, nature, solution, title, number,
                    decision_date, jurisdiction, formation,
                ) = row
                upsert_jade_node(
                    doc_id=doc_id,
                    nature=nature,
                    solution=solution,
                    title=title,
                    number=number,
                    decision_date=decision_date,
                    jurisdiction=jurisdiction,
                    formation=formation,
                )
                count += 1
                if count % 10000 == 0:
                    logger.info("JADE back-fill: %d nodes upserted", count)
        logger.info("JADE back-fill complete: %d nodes total", count)
    except Exception as exc:
        logger.error("Error during JADE graph back-fill: %s", exc)


def _populate_bofip_from_postgres(conn):
    """Back-fill :TaxGuidance nodes from the BOFIP PostgreSQL table (if it exists)."""
    graph = _get_graph()
    if graph is None:
        return
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT EXISTS (
                    SELECT 1 FROM information_schema.tables
                    WHERE table_schema = 'public'
                    AND table_name = 'bofip'
                )
                """
            )
            if not cur.fetchone()[0]:
                logger.info("BOFIP table not found in PostgreSQL, skipping back-fill.")
                return
        with conn.cursor(name="bofip_cursor") as cur:
            cur.execute(
                """
                SELECT DISTINCT ON (doc_id)
                    doc_id, nature, category, title, date
                FROM BOFIP
                ORDER BY doc_id, chunk_index
                """
            )
            count = 0
            for row in cur:
                doc_id, nature, category, title, date = row
                upsert_bofip_node(
                    doc_id=doc_id,
                    nature=nature,
                    category=category,
                    title=title,
                    date=date,
                )
                count += 1
                if count % 10000 == 0:
                    logger.info("BOFIP back-fill: %d nodes upserted", count)
        logger.info("BOFIP back-fill complete: %d nodes total", count)
    except Exception as exc:
        logger.error("Error during BOFIP graph back-fill: %s", exc)


# ---------------------------------------------------------------------------
# GraphRAG — Knowledge Graph builder using graphrag-sdk
# ---------------------------------------------------------------------------

def build_graphrag_knowledge_graph(llm_model: str | None = None):
    """Build a GraphRAG Knowledge Graph on top of the FalkorDB graph.

    This function leverages the ``graphrag-sdk`` library to enrich the graph
    with entity extraction and question-answering capabilities.

    The ontology passed to the SDK mirrors the hand-crafted one defined in this
    module so that both the raw Cypher graph and the GraphRAG overlay share the
    same node/relationship vocabulary.

    Args:
        llm_model: LLM model identifier to use for entity extraction.  When
            ``None`` the value is taken from the ``LLM_MODEL`` configuration.

    Returns:
        A ``KnowledgeGraph`` instance from ``graphrag_sdk`` (or ``None`` if the
        SDK or FalkorDB is unavailable).
    """
    try:
        from graphrag_sdk import KnowledgeGraph, Ontology  # noqa: PLC0415
        from graphrag_sdk.models.openai import OpenAiGenerativeModel  # noqa: PLC0415
        from graphrag_sdk.ontology import Edge, Node, Property  # noqa: PLC0415
    except ImportError:
        logger.warning(
            "graphrag-sdk is not installed. "
            "Install it with: pip install graphrag-sdk==0.8.2"
        )
        return None

    try:
        from config import API_KEY, API_URL, LLM_MODEL  # noqa: PLC0415
    except ImportError as exc:
        logger.error("Missing dependency for GraphRAG KG builder: %s", exc)
        return None

    model_name = llm_model or LLM_MODEL

    # ------------------------------------------------------------------
    # Define the ontology
    # ------------------------------------------------------------------
    ontology = Ontology()

    # Nodes
    legal_text_node = Node(
        label="LegalText",
        properties=[
            Property(name="doc_id", type="str", required=True, unique=True),
            Property(name="nature", type="str"),
            Property(name="category", type="str"),
            Property(name="ministry", type="str"),
            Property(name="status", type="str"),
            Property(name="title", type="str"),
            Property(name="full_title", type="str"),
            Property(name="number", type="str"),
            Property(name="start_date", type="str"),
            Property(name="end_date", type="str"),
        ],
    )
    judicial_decision_node = Node(
        label="JudicialDecision",
        properties=[
            Property(name="doc_id", type="str", required=True, unique=True),
            Property(name="nature", type="str"),
            Property(name="solution", type="str"),
            Property(name="title", type="str"),
            Property(name="number", type="str"),
            Property(name="decision_date", type="str"),
            Property(name="jurisdiction", type="str"),
            Property(name="formation", type="str"),
        ],
    )
    tax_guidance_node = Node(
        label="TaxGuidance",
        properties=[
            Property(name="doc_id", type="str", required=True, unique=True),
            Property(name="nature", type="str"),
            Property(name="category", type="str"),
            Property(name="title", type="str"),
            Property(name="date", type="str"),
        ],
    )
    legal_code_node = Node(
        label="LegalCode",
        properties=[
            Property(name="name", type="str", required=True, unique=True),
        ],
    )
    ministry_node = Node(
        label="Ministry",
        properties=[
            Property(name="name", type="str", required=True, unique=True),
        ],
    )
    jurisdiction_node = Node(
        label="Jurisdiction",
        properties=[
            Property(name="name", type="str", required=True, unique=True),
        ],
    )
    chunk_node = Node(
        label="Chunk",
        properties=[
            Property(name="chunk_id", type="str", required=True, unique=True),
            Property(name="source_type", type="str"),
        ],
    )

    for node in (
        legal_text_node,
        judicial_decision_node,
        tax_guidance_node,
        legal_code_node,
        ministry_node,
        jurisdiction_node,
        chunk_node,
    ):
        ontology.add_node(node)

    # Edges
    ontology.add_edge(
        Edge(relation="BELONGS_TO_CODE", source="LegalText", target="LegalCode")
    )
    ontology.add_edge(
        Edge(relation="BELONGS_TO_CODE", source="TaxGuidance", target="LegalCode")
    )
    ontology.add_edge(
        Edge(relation="ISSUED_BY", source="LegalText", target="Ministry")
    )
    ontology.add_edge(
        Edge(relation="REFERENCES", source="LegalText", target="LegalText",
             properties=[Property(name="type", type="str")])
    )
    ontology.add_edge(
        Edge(relation="DECIDED_BY", source="JudicialDecision", target="Jurisdiction")
    )
    ontology.add_edge(
        Edge(relation="PART_OF", source="Chunk", target="LegalText")
    )
    ontology.add_edge(
        Edge(relation="PART_OF", source="Chunk", target="JudicialDecision")
    )
    ontology.add_edge(
        Edge(relation="PART_OF", source="Chunk", target="TaxGuidance")
    )

    # ------------------------------------------------------------------
    # Instantiate the KnowledgeGraph
    # ------------------------------------------------------------------
    try:
        kwargs = {"host": FALKORDB_HOST, "port": FALKORDB_PORT}
        if FALKORDB_USERNAME:
            kwargs["username"] = FALKORDB_USERNAME
        if FALKORDB_PASSWORD:
            kwargs["password"] = FALKORDB_PASSWORD

        llm = OpenAiGenerativeModel(model=model_name, api_key=API_KEY, base_url=API_URL)

        kg = KnowledgeGraph(
            name=FALKORDB_GRAPH_NAME,
            ontology=ontology,
            model=llm,
            **kwargs,
        )
        logger.info(
            "GraphRAG KnowledgeGraph instantiated on graph '%s'", FALKORDB_GRAPH_NAME
        )
        return kg
    except Exception as exc:
        logger.error("Failed to build GraphRAG KnowledgeGraph: %s", exc)
        return None
