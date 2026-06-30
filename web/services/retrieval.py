from __future__ import annotations

import logging

from config import RETRIEVAL_OVERSAMPLING_FACTOR
from web.models.schemas import (
    ChunkResult,
    GraphData,
    SearchRequest,
    SearchResponse,
    SourceType,
)
from web.services.graph_search import get_related_doc_ids, get_subgraph
from web.services.vector_search import vector_search

logger = logging.getLogger(__name__)

GRAPH_RELATIONS = ["APPLIES_TO", "INTERPRETS", "REFERENCES"]


def _cross_encoder_rerank(query: str, results: list[ChunkResult], top_k: int) -> list[ChunkResult]:
    """Apply cross-encoder reranking to candidate results."""
    if not results:
        return results
    try:
        from web.services.reranker import rerank
        documents = [r.chunk_text for r in results]
        ranked = rerank(query, documents, top_k=top_k)
        return [results[idx] for idx, _score in ranked]
    except Exception as e:
        logger.warning("Reranker unavailable, falling back to vector similarity: %s", e)
        return results[:top_k]


def graphrag_search(conn, graph, request: SearchRequest) -> SearchResponse:
    """Full GraphRAG search: vector retrieval + graph augmentation + cross-encoder reranking."""
    oversample_k = request.top_k * RETRIEVAL_OVERSAMPLING_FACTOR

    # Step 1: Vector search (oversampled)
    results = vector_search(
        conn,
        query_text=request.query,
        source_types=request.source_types,
        top_k=oversample_k,
        date_start=request.date_start,
        date_end=request.date_end,
    )

    # Step 2: Graph augmentation (if FalkorDB available)
    graph_data = None
    if graph and results:
        top_doc_ids = list({r.doc_id for r in results[:5]})
        related_ids = get_related_doc_ids(graph, top_doc_ids, GRAPH_RELATIONS, limit=10)

        if related_ids:
            augmented_results = _fetch_graph_neighbors_text(conn, related_ids, results)
            results = _merge_results(results, augmented_results)

        all_ids = list({r.doc_id for r in results[:10]})
        graph_data = get_subgraph(graph, all_ids)

    # Step 3: Cross-encoder reranking
    results = _cross_encoder_rerank(request.query, results, request.top_k)

    # Apply confidence filter
    if request.min_confidence > 0:
        results = [r for r in results if r.similarity >= request.min_confidence]

    final_results = results[: request.top_k]
    return SearchResponse(
        results=final_results,
        graph=graph_data,
        total_results=len(final_results),
    )


def _fetch_graph_neighbors_text(conn, related_ids: list[str], existing: list[ChunkResult]) -> list[ChunkResult]:
    """Fetch chunk text for graph-discovered documents not already in results."""
    existing_ids = {r.doc_id for r in existing}
    new_ids = [rid for rid in related_ids if rid not in existing_ids]
    if not new_ids:
        return []

    cursor = conn.cursor()
    results: list[ChunkResult] = []

    for table, st in [("legi", SourceType.legi), ("jade", SourceType.jade), ("bofip", SourceType.bofip)]:
        cursor.execute(
            f"""
            SELECT DISTINCT ON (doc_id) doc_id, chunk_id, title, chunk_text
            FROM {table}
            WHERE doc_id = ANY(%s)
            ORDER BY doc_id, chunk_index
            """,
            (new_ids,),
        )
        for row in cursor.fetchall():
            results.append(
                ChunkResult(
                    doc_id=row[0],
                    chunk_id=row[1],
                    source_type=st,
                    title=row[2],
                    chunk_text=row[3][:500],
                    similarity=0.0,
                    metadata={"source": "graph_augmentation"},
                )
            )

    return results


def _merge_results(vector_results: list[ChunkResult], graph_results: list[ChunkResult]) -> list[ChunkResult]:
    """Merge vector results with graph-augmented results, sorted by similarity."""
    combined = list(vector_results)
    existing_ids = {(r.doc_id, r.chunk_id) for r in combined}

    for gr in graph_results:
        if (gr.doc_id, gr.chunk_id) not in existing_ids:
            gr.similarity = 0.3
            gr.metadata["source"] = "graph_augmentation"
            combined.append(gr)

    combined.sort(key=lambda r: r.similarity, reverse=True)
    return combined
