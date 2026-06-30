from __future__ import annotations

from web.models.schemas import GraphData, GraphEdge, GraphNode

NODE_LABELS = ["LegalText", "JudicialDecision", "TaxGuidance", "LegalCode", "Ministry", "Court", "TaxCode"]
RELATION_TYPES = ["APPLIES_TO", "INTERPRETS", "REFERENCES", "BELONGS_TO_CODE", "ISSUED_BY", "DECIDED_BY"]

SAFE_PROPS = {"doc_id", "title", "name", "nature", "category", "ministry", "status",
              "number", "start_date", "end_date", "decision_date", "jurisdiction",
              "formation", "solution", "publication_date", "subjects", "category_path",
              "confidence", "occurrence_count", "resolver_methods", "normalized_numbers"}


def _make_node(node_id: str, label: str, props: dict) -> GraphNode:
    safe_props = {k: v for k, v in props.items() if k in SAFE_PROPS}
    return GraphNode(
        id=node_id,
        label=label,
        doc_id=props.get("doc_id"),
        name=props.get("name"),
        title=props.get("title"),
        properties=safe_props,
    )


def _extract_node_id(node) -> str:
    """Extract a stable ID from a FalkorDB node result."""
    if hasattr(node, "properties"):
        props = node.properties or {}
        return props.get("doc_id") or props.get("name") or str(getattr(node, "id", ""))
    return str(node)


def _parse_result_set(result) -> GraphData:
    """Parse FalkorDB result_set where each row is [source_node, edge, target_node]."""
    nodes_map: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    if not result or not result.result_set:
        return GraphData()

    for row in result.result_set:
        if len(row) < 3:
            continue

        src_node, rel, tgt_node = row[0], row[1], row[2]

        if src_node and hasattr(src_node, "properties"):
            src_id = _extract_node_id(src_node)
            label = src_node.labels[0] if hasattr(src_node, "labels") and src_node.labels else "Unknown"
            props = dict(src_node.properties) if src_node.properties else {}
            nodes_map[src_id] = _make_node(src_id, label, props)

        if tgt_node and hasattr(tgt_node, "properties"):
            tgt_id = _extract_node_id(tgt_node)
            label = tgt_node.labels[0] if hasattr(tgt_node, "labels") and tgt_node.labels else "Unknown"
            props = dict(tgt_node.properties) if tgt_node.properties else {}
            nodes_map[tgt_id] = _make_node(tgt_id, label, props)

        if rel and src_node and tgt_node:
            src_id = _extract_node_id(src_node)
            tgt_id = _extract_node_id(tgt_node)
            rel_type = rel.relation if hasattr(rel, "relation") else "UNKNOWN"
            rel_props = dict(rel.properties) if hasattr(rel, "properties") and rel.properties else {}
            edges.append(GraphEdge(source=src_id, target=tgt_id, relation=rel_type, properties=rel_props))

    return GraphData(nodes=list(nodes_map.values()), edges=edges)


def get_neighbors(graph, doc_id: str, hops: int = 1, relation_types: list[str] | None = None, limit: int = 50) -> GraphData:
    """Get N-hop neighborhood of a document node."""
    rel_filter = ""
    if relation_types:
        rel_filter = ":" + "|".join(relation_types)

    query = f"""
        MATCH (n {{doc_id: $doc_id}})-[r{rel_filter}]-(m)
        RETURN n, r, m
        LIMIT {limit}
    """
    try:
        result = graph.query(query, params={"doc_id": doc_id})
    except Exception:
        return GraphData()

    return _parse_result_set(result)


def get_subgraph(graph, doc_ids: list[str]) -> GraphData:
    """Get the subgraph connecting a set of documents."""
    if not doc_ids:
        return GraphData()

    query = """
        MATCH (n)-[r]-(m)
        WHERE n.doc_id IN $doc_ids OR m.doc_id IN $doc_ids
        RETURN n, r, m
        LIMIT 200
    """
    try:
        result = graph.query(query, params={"doc_ids": doc_ids})
    except Exception:
        return GraphData()

    return _parse_result_set(result)


def get_document_context(graph, doc_id: str) -> GraphData:
    """Get all direct relationships for a single document."""
    query = """
        MATCH (n {doc_id: $doc_id})-[r]-(m)
        RETURN n, r, m
        LIMIT 100
    """
    try:
        result = graph.query(query, params={"doc_id": doc_id})
    except Exception:
        return GraphData()

    return _parse_result_set(result)


def get_related_doc_ids(graph, doc_ids: list[str], relation_types: list[str] | None = None, limit: int = 20) -> list[str]:
    """Get doc_ids of related nodes (for graph augmentation in retrieval)."""
    if not doc_ids:
        return []

    rel_filter = ""
    if relation_types:
        rel_filter = ":" + "|".join(relation_types)

    query = f"""
        MATCH (n)-[r{rel_filter}]-(m)
        WHERE n.doc_id IN $doc_ids AND m.doc_id IS NOT NULL AND NOT m.doc_id IN $doc_ids
        RETURN DISTINCT m.doc_id AS related_id
        LIMIT {limit}
    """
    try:
        result = graph.query(query, params={"doc_ids": doc_ids})
        return [row[0] for row in result.result_set if row[0]]
    except Exception:
        return []
