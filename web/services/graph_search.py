from __future__ import annotations

from web.models.schemas import GraphData, GraphEdge, GraphNode

NODE_LABELS = ["LegalText", "JudicialDecision", "TaxGuidance", "LegalCode", "Ministry", "Court", "TaxCode"]
RELATION_TYPES = ["APPLIES_TO", "INTERPRETS", "REFERENCES", "BELONGS_TO_CODE", "ISSUED_BY", "DECIDED_BY"]


def _node_id(node) -> str:
    """Extract a stable ID from a FalkorDB node."""
    props = node.properties
    return props.get("doc_id") or props.get("name") or str(node.id)


def _parse_node(node) -> GraphNode:
    """Convert a FalkorDB node to our schema."""
    props = dict(node.properties) if node.properties else {}
    node_id = props.get("doc_id") or props.get("name") or str(node.id)
    label = node.labels[0] if node.labels else "Unknown"
    safe_props = {}
    for k, v in props.items():
        if k in ("texts", "embeddings", "chunk_texts"):
            continue
        safe_props[k] = v
    return GraphNode(
        id=node_id,
        label=label,
        doc_id=props.get("doc_id"),
        name=props.get("name"),
        title=props.get("title"),
        properties=safe_props,
    )


def _parse_edge(rel, source_id: str, target_id: str) -> GraphEdge:
    """Convert a FalkorDB relationship to our schema."""
    props = dict(rel.properties) if rel.properties else {}
    return GraphEdge(
        source=source_id,
        target=target_id,
        relation=rel.relation if hasattr(rel, "relation") else str(type(rel).__name__),
        properties=props,
    )


def get_neighbors(graph, doc_id: str, hops: int = 1, relation_types: list[str] | None = None, limit: int = 50) -> GraphData:
    """Get N-hop neighborhood of a document node."""
    rel_filter = ""
    if relation_types:
        rel_filter = ":" + "|".join(relation_types)

    query = f"""
        MATCH (n {{doc_id: $doc_id}})-[r{rel_filter}*1..{hops}]-(m)
        RETURN n, r, m
        LIMIT {limit}
    """
    try:
        result = graph.query(query, params={"doc_id": doc_id})
    except Exception:
        return GraphData()

    return _build_graph_data(result)


def get_subgraph(graph, doc_ids: list[str]) -> GraphData:
    """Get the subgraph connecting a set of documents."""
    query = """
        MATCH (n)-[r]-(m)
        WHERE n.doc_id IN $doc_ids AND m.doc_id IN $doc_ids
        RETURN n, r, m
    """
    try:
        result = graph.query(query, params={"doc_ids": doc_ids})
    except Exception:
        return GraphData()

    return _build_graph_data(result)


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

    return _build_graph_data(result)


def get_related_doc_ids(graph, doc_ids: list[str], relation_types: list[str] | None = None, limit: int = 20) -> list[str]:
    """Get doc_ids of related nodes (for graph augmentation in retrieval)."""
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


def _build_graph_data(result) -> GraphData:
    """Parse a FalkorDB query result into GraphData."""
    nodes_map: dict[str, GraphNode] = {}
    edges: list[GraphEdge] = []

    for row in result.result_set:
        for item in row:
            if item is None:
                continue
            if hasattr(item, "labels"):
                node = _parse_node(item)
                nodes_map[node.id] = node
            elif hasattr(item, "relation"):
                src_id = None
                tgt_id = None
                if hasattr(item, "src_node"):
                    src_id = _node_id(item.src_node) if item.src_node else None
                    tgt_id = _node_id(item.dest_node) if item.dest_node else None
                if src_id and tgt_id:
                    edges.append(_parse_edge(item, src_id, tgt_id))
            elif isinstance(item, list):
                for sub in item:
                    if hasattr(sub, "labels"):
                        node = _parse_node(sub)
                        nodes_map[node.id] = node
                    elif hasattr(sub, "relation"):
                        if hasattr(sub, "src_node") and sub.src_node and sub.dest_node:
                            edges.append(_parse_edge(sub, _node_id(sub.src_node), _node_id(sub.dest_node)))

    return GraphData(nodes=list(nodes_map.values()), edges=edges)
