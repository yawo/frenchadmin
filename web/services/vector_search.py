from __future__ import annotations

import logging
from datetime import date
from typing import Any

from config import ENABLE_HYBRID_SEARCH, FTS_WEIGHT, RRF_K
from web.models.schemas import (
    ChunkResult,
    CrossRefListResponse,
    CrossReference,
    DocumentDetail,
    SourceType,
)
from web.services.embedding import EMBEDDING_COLUMN, embed_query

logger = logging.getLogger(__name__)

TABLE_MAP = {
    SourceType.legi: "legi",
    SourceType.jade: "jade",
    SourceType.bofip: "bofip",
}

METADATA_COLUMNS = {
    "legi": ["nature", "category", "ministry", "status", "number", "start_date", "end_date"],
    "jade": ["nature", "solution", "number", "decision_date", "jurisdiction", "formation"],
    "bofip": ["contenu_type", "document_number", "publication_date", "category_path"],
}


def vector_search(
    conn,
    query_text: str,
    source_types: list[SourceType] | None = None,
    top_k: int = 10,
    date_start: date | None = None,
    date_end: date | None = None,
) -> list[ChunkResult]:
    """Hybrid search: vector similarity + full-text search fused via RRF."""
    vector_results = _vector_search_inner(conn, query_text, source_types, top_k, date_start, date_end)

    if not ENABLE_HYBRID_SEARCH:
        return vector_results

    targets = source_types or [SourceType.legi, SourceType.jade, SourceType.bofip]
    fts_results = _fts_search(conn, query_text, targets, top_k)

    if not fts_results:
        return vector_results

    fused = _reciprocal_rank_fusion(
        [vector_results, fts_results],
        k=RRF_K,
        weights=[1.0, FTS_WEIGHT],
    )
    return fused[:top_k]


def _vector_search_inner(
    conn,
    query_text: str,
    source_types: list[SourceType] | None = None,
    top_k: int = 10,
    date_start: date | None = None,
    date_end: date | None = None,
) -> list[ChunkResult]:
    """Cosine similarity search across specified tables."""
    embedding = embed_query(query_text)
    targets = source_types or [SourceType.legi, SourceType.jade, SourceType.bofip]
    per_table_k = max(3, top_k // len(targets) + 1)
    results: list[ChunkResult] = []

    cursor = conn.cursor()
    for st in targets:
        table = TABLE_MAP[st]
        meta_cols = METADATA_COLUMNS[table]
        meta_select = ", ".join(f"t.{c}" for c in meta_cols)

        date_filter = ""
        date_params: list = []
        if date_start and table == "legi":
            date_filter += " AND t.start_date::date >= %s"
            date_params.append(date_start)
        if date_end and table == "legi":
            date_filter += " AND t.end_date::date <= %s"
            date_params.append(date_end)

        query = f"""
            SELECT
                t.doc_id,
                t.chunk_id,
                t.title,
                t.chunk_text,
                1 - (t."{EMBEDDING_COLUMN}" <=> %s::vector) AS similarity,
                {meta_select}
            FROM {table} t
            WHERE t."{EMBEDDING_COLUMN}" IS NOT NULL
            {date_filter}
            ORDER BY t."{EMBEDDING_COLUMN}" <=> %s::vector
            LIMIT %s
        """
        params = [embedding] + date_params + [embedding, per_table_k]
        cursor.execute(query, params)
        rows = cursor.fetchall()
        col_names = [desc[0] for desc in cursor.description]

        for row in rows:
            row_dict = dict(zip(col_names, row))
            metadata = {c: row_dict.get(c) for c in meta_cols if row_dict.get(c) is not None}
            for k, v in metadata.items():
                if isinstance(v, date):
                    metadata[k] = v.isoformat()
            results.append(
                ChunkResult(
                    doc_id=row_dict["doc_id"],
                    chunk_id=row_dict["chunk_id"],
                    source_type=st,
                    title=row_dict.get("title"),
                    chunk_text=row_dict["chunk_text"],
                    similarity=float(row_dict["similarity"]),
                    metadata=metadata,
                )
            )

    results.sort(key=lambda r: r.similarity, reverse=True)
    return results[:top_k]


def _fts_search(
    conn,
    query_text: str,
    source_types: list[SourceType],
    top_k: int,
) -> list[ChunkResult]:
    """Full-text search using PostgreSQL tsvector/tsquery."""
    per_table_k = max(3, top_k // len(source_types) + 1)
    results: list[ChunkResult] = []

    cursor = conn.cursor()
    for st in source_types:
        table = TABLE_MAP[st]
        meta_cols = METADATA_COLUMNS[table]
        meta_select = ", ".join(f"t.{c}" for c in meta_cols)

        query = f"""
            SELECT
                t.doc_id,
                t.chunk_id,
                t.title,
                t.chunk_text,
                ts_rank_cd(t.chunk_tsv, query) AS fts_score,
                {meta_select}
            FROM {table} t,
                 plainto_tsquery('french', %s) query
            WHERE t.chunk_tsv @@ query
            ORDER BY ts_rank_cd(t.chunk_tsv, query) DESC
            LIMIT %s
        """
        try:
            cursor.execute(query, [query_text, per_table_k])
            rows = cursor.fetchall()
            col_names = [desc[0] for desc in cursor.description]

            for row in rows:
                row_dict = dict(zip(col_names, row))
                metadata = {c: row_dict.get(c) for c in meta_cols if row_dict.get(c) is not None}
                for k, v in metadata.items():
                    if isinstance(v, date):
                        metadata[k] = v.isoformat()
                results.append(
                    ChunkResult(
                        doc_id=row_dict["doc_id"],
                        chunk_id=row_dict["chunk_id"],
                        source_type=st,
                        title=row_dict.get("title"),
                        chunk_text=row_dict["chunk_text"],
                        similarity=float(row_dict["fts_score"]),
                        metadata=metadata,
                    )
                )
        except Exception as e:
            logger.debug("FTS search skipped for %s: %s", table, e)
            conn.rollback()

    results.sort(key=lambda r: r.similarity, reverse=True)
    return results[:top_k]


def _reciprocal_rank_fusion(
    result_lists: list[list[ChunkResult]],
    k: int = 60,
    weights: list[float] | None = None,
) -> list[ChunkResult]:
    """Fuse multiple ranked result lists using Reciprocal Rank Fusion."""
    if not weights:
        weights = [1.0] * len(result_lists)

    scores: dict[tuple[str, str], float] = {}
    best_result: dict[tuple[str, str], ChunkResult] = {}

    for weight, results in zip(weights, result_lists):
        for rank, result in enumerate(results):
            key = (result.doc_id, result.chunk_id)
            rrf_score = weight / (k + rank + 1)
            scores[key] = scores.get(key, 0.0) + rrf_score
            if key not in best_result:
                best_result[key] = result

    sorted_keys = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)
    fused = []
    for key in sorted_keys:
        result = best_result[key]
        result.similarity = scores[key]
        fused.append(result)

    return fused


def get_document_by_id(conn, source_type: SourceType, doc_id: str) -> DocumentDetail | None:
    """Fetch full document detail with all chunks."""
    table = TABLE_MAP[source_type]
    meta_cols = METADATA_COLUMNS[table]
    meta_select = ", ".join(meta_cols)

    cursor = conn.cursor()
    cursor.execute(
        f"SELECT chunk_id, chunk_index, title, chunk_text, {meta_select} FROM {table} WHERE doc_id = %s ORDER BY chunk_index",
        (doc_id,),
    )
    rows = cursor.fetchall()
    if not rows:
        return None

    col_names = [desc[0] for desc in cursor.description]
    chunks = []
    title = None
    metadata: dict[str, Any] = {}

    for row in rows:
        row_dict = dict(zip(col_names, row))
        if title is None:
            title = row_dict.get("title")
            metadata = {c: row_dict.get(c) for c in meta_cols if row_dict.get(c) is not None}
            for k, v in metadata.items():
                if isinstance(v, date):
                    metadata[k] = v.isoformat()
        chunks.append({
            "chunk_id": row_dict["chunk_id"],
            "chunk_index": row_dict.get("chunk_index"),
            "chunk_text": row_dict["chunk_text"],
        })

    crossrefs = _get_crossrefs_for_doc(conn, source_type, doc_id)
    return DocumentDetail(
        doc_id=doc_id,
        source_type=source_type,
        title=title,
        metadata=metadata,
        chunks=chunks,
        cross_references=crossrefs,
    )


def _get_crossrefs_for_doc(conn, source_type: SourceType, doc_id: str) -> list[CrossReference]:
    """Get cross-references where this doc is source OR target."""
    cursor = conn.cursor()
    refs: list[CrossReference] = []

    # As source
    cursor.execute(
        """
        SELECT source_type, source_doc_id, target_legi_doc_id, relation_kind,
               best_confidence, occurrence_count, resolver_methods,
               source_chunk_ids, normalized_numbers, source_date, explain
        FROM cross_reference_legi_edges
        WHERE source_doc_id = %s AND source_type = %s
        ORDER BY best_confidence DESC
        """,
        (doc_id, source_type.value),
    )
    for row in cursor.fetchall():
        refs.append(_row_to_crossref(row))

    # As target (LEGI)
    if source_type == SourceType.legi:
        cursor.execute(
            """
            SELECT source_type, source_doc_id, target_legi_doc_id, relation_kind,
                   best_confidence, occurrence_count, resolver_methods,
                   source_chunk_ids, normalized_numbers, source_date, explain
            FROM cross_reference_legi_edges
            WHERE target_legi_doc_id = %s
            ORDER BY best_confidence DESC
            """,
            (doc_id,),
        )
        for row in cursor.fetchall():
            refs.append(_row_to_crossref(row))

    return refs


def _row_to_crossref(row) -> CrossReference:
    return CrossReference(
        source_type=SourceType(row[0]),
        source_doc_id=row[1],
        target_legi_doc_id=row[2],
        relation_kind=row[3],
        best_confidence=float(row[4]),
        occurrence_count=row[5],
        resolver_methods=row[6] or [],
        source_chunk_ids=row[7] or [],
        normalized_numbers=row[8] or [],
        source_date=row[9],
        explain=row[10],
    )


def get_cross_references(
    conn,
    source_type: SourceType | None = None,
    target_doc_id: str | None = None,
    source_doc_id: str | None = None,
    min_confidence: float = 0.0,
    page: int = 1,
    page_size: int = 20,
) -> CrossRefListResponse:
    """Paginated cross-reference listing with filters."""
    cursor = conn.cursor()
    conditions = ["best_confidence >= %s"]
    params: list = [min_confidence]

    if source_type:
        conditions.append("source_type = %s")
        params.append(source_type.value)
    if target_doc_id:
        conditions.append("target_legi_doc_id = %s")
        params.append(target_doc_id)
    if source_doc_id:
        conditions.append("source_doc_id = %s")
        params.append(source_doc_id)

    where = " AND ".join(conditions)

    cursor.execute(f"SELECT COUNT(*) FROM cross_reference_legi_edges WHERE {where}", params)
    total = cursor.fetchone()[0]

    offset = (page - 1) * page_size
    cursor.execute(
        f"""
        SELECT source_type, source_doc_id, target_legi_doc_id, relation_kind,
               best_confidence, occurrence_count, resolver_methods,
               source_chunk_ids, normalized_numbers, source_date, explain
        FROM cross_reference_legi_edges
        WHERE {where}
        ORDER BY best_confidence DESC
        LIMIT %s OFFSET %s
        """,
        params + [page_size, offset],
    )
    items = [_row_to_crossref(row) for row in cursor.fetchall()]

    return CrossRefListResponse(items=items, total=total, page=page, page_size=page_size)
