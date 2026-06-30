"""Cross-reference inference pipeline.

Orchestrates:
1. LEGI reference catalog refresh
2. Source document aggregation (JADE/BOFIP)
3. Mention extraction at chunk level
4. Deterministic resolution
5. Mention dedup + edge aggregation
6. FalkorDB graph injection
"""

import hashlib
import re
import time
from typing import Optional

from config import EMBEDDING_MODEL, get_logger
from database.database_manage import get_connection
from database.cross_reference_manage import (
    aggregate_and_upsert_edges,
    create_cross_reference_tables,
    delete_mentions_and_edges_for_doc,
    get_source_state,
    insert_mentions_batch,
    refresh_legi_reference_catalog,
    upsert_source_state_hash,
)
from crossreference._version import PIPELINE_VERSION
from database.graph_manage import init_graph_schema, inject_cross_reference_edges
from crossreference.alias_detector import invalidate_extended_alias_cache
from crossreference.extractor import extract_article_mentions
from crossreference.resolver import resolve_article
from crossreference.normalizer import normalize_article_number, loose_normalized_number
from crossreference.confidence import score_confidence

logger = get_logger(__name__)

RELATION_KIND = {
    "jade": "applies_to",
    "bofip": "interprets",
}

_VU_PATTERN = re.compile(
    r'\b(?:Vu\s+la|Vu\s+le|Vu\s+.*?proc[eé]dure|VU|Consid[eé]rant|Aux\s+termes\s+de\s+l|Sur\s+le\s+(?:moyen|fondement|bien-fond[eé]))\b',
    re.IGNORECASE,
)


def infer_crossreferences(
    source: str = "all",
    model: str = EMBEDDING_MODEL,
    debug: bool = False,
):
    """Run cross-reference inference pipeline.

    Args:
        source: 'jade', 'bofip', or 'all'
        model: embedding model used for semantic fallback
        debug: enable verbose logging

    Returns:
        dict with run summary metrics and failure count.
    """
    started = time.perf_counter()

    # 0. Init graph schema
    init_graph_schema()
    create_cross_reference_tables()

    # 1. Refresh LEGI reference catalog
    logger.info("Refreshing legi_reference_catalog")
    catalog_hash = refresh_legi_reference_catalog()
    invalidate_extended_alias_cache()

    # 2. Process source documents
    sources = ["jade", "bofip"] if source == "all" else [source]

    total_docs = 0
    total_mentions = 0
    total_accepted = 0
    total_edges = 0
    failed_docs = 0

    for src in sources:
        if src == "jade":
            docs = _aggregate_jade_documents()
        elif src == "bofip":
            docs = _aggregate_bofip_documents()
        else:
            logger.warning(f"Unknown source type: {src}")
            continue

        logger.info(f"Processing {len(docs)} {src.upper()} documents")

        for doc_info in docs:
            doc_started = time.perf_counter()

            try:
                # Check incremental: skip when source content, catalog content,
                # AND inference pipeline version are all unchanged. Bumping
                # PIPELINE_VERSION on any extractor/resolver/normalizer change
                # invalidates every stored state row at deploy time, forcing a
                # full reprocess even when LEGI and JADE/BOFIP have not moved.
                current_hash = doc_info["source_hash"]
                stored_state = _get_stored_state(src, doc_info["doc_id"])
                hashes_match = (
                    stored_state
                    and stored_state["source_hash"] == current_hash
                    and stored_state["catalog_hash"] == catalog_hash
                    and stored_state.get("pipeline_version") == PIPELINE_VERSION
                )
                if hashes_match and stored_state.get("graph_sync_ok"):
                    logger.debug(f"Skipping {src} doc {doc_info['doc_id']}: hash unchanged")
                    continue
                if hashes_match and not stored_state.get("graph_sync_ok"):
                    # Rebuild mentions/edges before retrying graph sync to avoid stale cache
                    mentions = _process_source_document(
                        source_type=src,
                        doc_info=doc_info,
                        catalog_hash=catalog_hash,
                        model=model,
                        debug=debug,
                    )
                    with get_connection() as conn:
                        cursor = conn.cursor()
                        delete_mentions_and_edges_for_doc(cursor, src, doc_info["doc_id"])
                        insert_mentions_batch(cursor, mentions)
                        aggregate_and_upsert_edges(cursor, src, doc_info["doc_id"])
                        conn.commit()
                    
                    graph_sync_ok = inject_cross_reference_edges(src, doc_info["doc_id"])
                    _upsert_source_state(
                        source_type=src,
                        source_doc_id=doc_info["doc_id"],
                        source_hash=current_hash,
                        catalog_hash=catalog_hash,
                        graph_sync_ok=graph_sync_ok,
                    )
                    logger.debug(
                        f"[{src}] {doc_info['doc_id']}: rebuilt mentions and retried graph sync "
                        f"(graph_sync_ok={graph_sync_ok})"
                    )
                    if not graph_sync_ok:
                        failed_docs += 1
                        logger.error(
                            f"[{src}] Graph sync retry failed for doc {doc_info['doc_id']}"
                        )
                    continue

                # Rebuild from scratch for this document
                mentions = _process_source_document(
                    source_type=src,
                    doc_info=doc_info,
                    catalog_hash=catalog_hash,
                    model=model,
                    debug=debug,
                )

                doc_elapsed = time.perf_counter() - doc_started
                accepted = [m for m in mentions if m["is_accepted"]]

                total_docs += 1
                total_mentions += len(mentions)
                total_accepted += len(accepted)

                logger.info(
                    f"[{src}] {doc_info['doc_id']}: "
                    f"{len(mentions)} mentions, {len(accepted)} accepted, "
                    f"{doc_elapsed:.1f}s"
                )
            except Exception as e:
                failed_docs += 1
                logger.error(
                    f"[{src}] Failed to process doc {doc_info['doc_id']}: {e}"
                )
                continue

        # After processing all docs for this source type, count edges
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) FROM cross_reference_legi_edges
                WHERE source_type = %s
            """, (src,))
            total_edges += cursor.fetchone()[0]

    elapsed = time.perf_counter() - started
    logger.info(
        f"Cross-reference inference complete: "
        f"{total_docs} docs, {total_mentions} mentions, "
        f"{total_accepted} accepted, {total_edges} edges, "
        f"{elapsed:.1f}s total"
    )
    if failed_docs:
        logger.error(
            f"Cross-reference inference completed with {failed_docs} failed document(s)"
        )
    return {
        "processed_docs": total_docs,
        "failed_docs": failed_docs,
        "mentions": total_mentions,
        "accepted_mentions": total_accepted,
        "edges": total_edges,
        "elapsed_seconds": elapsed,
    }


def _get_stored_state(source_type: str, source_doc_id: str) -> Optional[dict]:
    with get_connection() as conn:
        cursor = conn.cursor()
        return get_source_state(cursor, source_type, source_doc_id)


def _upsert_source_state(
    source_type: str,
    source_doc_id: str,
    source_hash: str,
    catalog_hash: str,
    graph_sync_ok: bool,
):
    with get_connection() as conn:
        cursor = conn.cursor()
        upsert_source_state_hash(
            cursor,
            source_type,
            source_doc_id,
            source_hash,
            catalog_hash=catalog_hash,
            graph_sync_ok=graph_sync_ok,
            pipeline_version=PIPELINE_VERSION,
        )
        conn.commit()


def _aggregate_jade_documents() -> list[dict]:
    """Aggregate JADE chunk rows into document-level records."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                doc_id,
                MIN(number) AS number,
                MIN(title) AS title,
                MIN(jurisdiction) AS jurisdiction,
                MIN(formation) AS formation,
                MIN(decision_date)::date AS source_date,
                MD5(string_agg(chunk_xxh64, '' ORDER BY chunk_index)) AS source_hash
            FROM jade
            WHERE decision_date IS NOT NULL
            GROUP BY doc_id
        """)

        docs = []
        for row in cursor.fetchall():
            docs.append({
                "doc_id": row[0],
                "number": row[1],
                "title": row[2],
                "jurisdiction": row[3],
                "formation": row[4],
                "source_date": row[5],
                "source_hash": row[6],
            })
        return docs


def _aggregate_bofip_documents() -> list[dict]:
    """Aggregate BOFIP chunk rows into document-level records."""
    with get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT
                doc_id,
                MIN(contenu_id) AS contenu_id,
                MIN(document_number) AS document_number,
                MIN(title) AS title,
                MIN(category_path) AS category_path,
                MIN(publication_date)::date AS source_date,
                MD5(string_agg(chunk_xxh64, '' ORDER BY chunk_index)) AS source_hash
            FROM bofip
            WHERE publication_date IS NOT NULL
            GROUP BY doc_id
        """)

        docs = []
        for row in cursor.fetchall():
            docs.append({
                "doc_id": row[0],
                "contenu_id": row[1],
                "document_number": row[2],
                "title": row[3],
                "category_path": row[4],
                "source_date": row[5],
                "source_hash": row[6],
            })
        return docs


def _get_source_chunks(source_type: str, doc_id: str) -> list[dict]:
    """Fetch chunk-level rows for a source document.

    extraction_text is the field the regex extractor reads. Per
    CROSSREFERENCE.md §6.1 / §6.2:
    - JADE stores the full body in ``text`` and duplicates it per chunk row,
      so we MUST use ``chunk_text`` (per-chunk, title + chunk slice) for
      regex extraction. Otherwise every JADE mention is duplicated across
      every chunk of the decision and the resulting offsets are
      document-global instead of chunk-local.
    - BOFIP stores per-chunk raw content in ``text``; ``chunk_text`` is the
      enriched embedding form. We keep ``text`` for extraction.
    """
    with get_connection() as conn:
        cursor = conn.cursor()
        if source_type == "jade":
            cursor.execute(
                """
                SELECT chunk_id, chunk_index, text, chunk_text
                FROM jade
                WHERE doc_id = %s
                ORDER BY chunk_index
                """,
                (doc_id,),
            )
            return [
                {
                    "chunk_id": row[0],
                    "chunk_index": row[1],
                    "extraction_text": row[3] or "",
                    "chunk_text": row[3] or "",
                }
                for row in cursor.fetchall()
            ]

        cursor.execute(
            """
            SELECT chunk_id, chunk_index, text, chunk_text
            FROM bofip
            WHERE doc_id = %s
            ORDER BY chunk_index
            """,
            (doc_id,),
        )
        return [
            {
                "chunk_id": row[0],
                "chunk_index": row[1],
                "extraction_text": row[2] or "",
                "chunk_text": row[3] or "",
            }
            for row in cursor.fetchall()
        ]


def _process_source_document(
    source_type: str,
    doc_info: dict,
    catalog_hash: str,
    model: str,
    debug: bool = False,
) -> list[dict]:
    """Process one source document: extract mentions, resolve, return mention list."""
    doc_id = doc_info["doc_id"]
    source_date = doc_info["source_date"]
    source_hash = doc_info["source_hash"]
    relation_kind = RELATION_KIND[source_type]
    chunks = _get_source_chunks(source_type, doc_id)

    # Extract mentions at chunk level to preserve provenance.
    raw_mentions = []
    for chunk in chunks:
        extraction_text = chunk["extraction_text"]
        chunk_text = chunk["chunk_text"] or extraction_text
        for (matched_text, article_token, match_start, match_end,
             context_window) in extract_article_mentions(extraction_text):
            # Normalize from the clean article_token, not the rich matched_text.
            # matched_text still carries the code-name tail for provenance and
            # for alias_detector.extract_code_family_from_mention.
            normalized = normalize_article_number(article_token)
            semantic_context = context_window
            if source_type == "bofip":
                semantic_context = _enrich_bofip_context(context_window, chunk_text)
            raw_mentions.append({
                "source_chunk_id": chunk["chunk_id"],
                "source_chunk_index": chunk["chunk_index"],
                "matched_text": matched_text,
                "article_token": article_token,
                "match_start": match_start,
                "match_end": match_end,
                "context_window": semantic_context,
                "chunk_text": extraction_text,
                "normalized_number": normalized,
            })

    repeated_in_chunks_map = {}
    for raw in raw_mentions:
        normalized = raw["normalized_number"]
        if not normalized:
            continue
        # Track (normalized_number, chunk_id) pair to detect article reuse across chunks
        key = (normalized, raw["source_chunk_id"])
        repeated_in_chunks_map.setdefault(normalized, set()).add(key)

    # Resolve each mention
    mentions = []
    for raw in raw_mentions:
        result = resolve_article(
            article_token=raw["article_token"],
            matched_text=raw["matched_text"],
            source_date=source_date,
            context_text=raw["context_window"],
            source_type=source_type,
            model=model,
        )

        # Score confidence
        detected_alias = result["explain"].get("detected_code_alias")
        detected_family = result["explain"].get("detected_code_family")
        semantic_similarity = result.get("explain", {}).get("cosine_similarity")
        # Boost if this normalized_number appears in multiple different chunks
        repeated_in_chunks = bool(
            raw["normalized_number"]
            and len(repeated_in_chunks_map.get(raw["normalized_number"], set())) > 1
        )
        confidence, explain_detail = score_confidence(
            resolver_method=result["resolver_method"],
            source_type=source_type,
            detected_code_alias=detected_alias,
            is_generic=_is_generic_ref(raw["matched_text"]),
            repeated_in_chunks=repeated_in_chunks,
            mention_in_vu_section=(
                _is_in_vu_section(raw["chunk_text"], raw["match_start"])
                if source_type == "jade"
                else False
            ),
            semantic_similarity=semantic_similarity,
        )

        # Acceptance threshold: lower threshold to accept fuzzy + semantic matches
        # Confidence scoring now properly penalizes low-signal mentions.
        # Threshold 0.55 allows fuzzy (0.78) + semantic (0.65) with adjustments.
        # This increases recall without sacrificing precision (filtered mentions still examined).
        acceptance_threshold = 0.55

        is_accepted = confidence >= acceptance_threshold

        mention = {
            "mention_hash": _compute_mention_hash(
                source_type, doc_id, raw["source_chunk_id"],
                raw["match_start"], raw["match_end"],
                raw["matched_text"],
            ),
            "source_type": source_type,
            "source_doc_id": doc_id,
            "source_chunk_id": raw["source_chunk_id"],
            "source_chunk_index": raw["source_chunk_index"],
            "source_date": source_date,
            "source_hash": source_hash,
            "source_title": doc_info.get("title"),
            "source_secondary_id": (
                doc_info.get("contenu_id")
                or doc_info.get("number")
                or doc_info.get("document_number")
            ),
            "relation_kind": relation_kind,
            "matched_text": raw["matched_text"],
            "match_start": raw["match_start"],
            "match_end": raw["match_end"],
            "normalized_number": raw["normalized_number"] or None,
            "normalized_number_loose": (
                loose_normalized_number(raw["normalized_number"])
                if raw["normalized_number"]
                else None
            ),
            "detected_code_alias": detected_alias,
            "detected_code_family": detected_family,
            "detected_parent_text_ids": result["explain"].get("detected_parent_text_ids", []),
            "target_legi_doc_id": result["target_legi_doc_id"],
            "target_parent_text_id": result["target_parent_text_id"],
            "target_article_number": result["target_article_number"],
            "target_start_date": result["target_start_date"],
            "target_end_date": result["target_end_date"],
            "resolver_stage": "resolved" if result["target_legi_doc_id"] else "unresolved",
            "resolver_method": result["resolver_method"],
            "confidence": confidence,
            "is_accepted": is_accepted,
            "context_window": raw["context_window"],
            "explain": {**result.get("explain", {}), **explain_detail},
        }
        mentions.append(mention)

    # Delete old data, insert mentions, and aggregate edges in single transaction
    with get_connection() as conn:
        cursor = conn.cursor()
        delete_mentions_and_edges_for_doc(cursor, source_type, doc_id)
        insert_mentions_batch(cursor, mentions)
        aggregate_and_upsert_edges(cursor, source_type, doc_id)
        conn.commit()

    # Inject graph edges and only then mark source state.
    graph_sync_ok = inject_cross_reference_edges(source_type, doc_id)
    _upsert_source_state(
        source_type=source_type,
        source_doc_id=doc_id,
        source_hash=source_hash,
        catalog_hash=catalog_hash,
        graph_sync_ok=graph_sync_ok,
    )
    if not graph_sync_ok:
        logger.warning(f"Graph sync incomplete for {source_type}:{doc_id}; will retry on next run")

    return mentions


def _is_generic_ref(raw_text: str) -> bool:
    normalized = normalize_article_number(raw_text)
    return bool(re.fullmatch(r"\d{1,2}(?:\s+(?:BIS|TER))?", normalized))


def _enrich_bofip_context(context_window: str, chunk_text: str) -> str:
    """Prefix BOFIP local context with metadata-like header lines from chunk_text."""
    if not chunk_text:
        return context_window
    header = []
    for line in chunk_text.splitlines()[:5]:
        cleaned = line.strip()
        if cleaned:
            header.append(cleaned)
    header_text = "\n".join(header)
    if not header_text:
        return context_window
    return f"{header_text}\n{context_window}"


def _is_in_vu_section(chunk_text: str, match_start: int) -> bool:
    """Check if mention appears near VU-like section markers in local chunk context."""
    if match_start < 0:
        return False
    window = chunk_text[max(0, match_start - 500):match_start]
    return bool(_VU_PATTERN.search(window))


def _compute_mention_hash(
    source_type, source_doc_id, source_chunk_id,
    match_start, match_end, matched_text,
) -> str:
    """Hash identifies source mention location, not resolution outcome.
    
    Hashing only source-level attributes allows detection of mention deduplication
    across resolution updates without creating duplicates on re-resolution.
    """
    return hashlib.sha1(
        "|".join([
            source_type,
            source_doc_id,
            source_chunk_id,
            str(match_start),
            str(match_end),
            matched_text,
        ]).encode("utf-8")
    ).hexdigest()
