"""Cross-reference storage layer for JADE/BOFIP -> LEGI inference.

Manages four PostgreSQL objects:
- legi_reference_catalog: precomputed versioned target lookup
- cross_reference_legi_mentions: one row per extracted source mention
- cross_reference_legi_edges: aggregated accepted edges for RAG/graphRAG
- cross_reference_source_state: per-source-doc hash for incremental skipping
"""

import json
import hashlib
import re
import time

import psycopg2
import psycopg2.extras
from unidecode import unidecode

from config import (
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
    get_logger,
)
from database.database_manage import get_connection

logger = get_logger(__name__)
_SOURCE_STATE_COLUMNS_ENSURED = False
_CATALOG_STREAM_FETCH_SIZE = 2000
_CATALOG_INSERT_BATCH_SIZE = 1000


def _ensure_source_state_columns(cursor):
    """Ensure source-state optional columns exist exactly once per process."""
    global _SOURCE_STATE_COLUMNS_ENSURED
    if _SOURCE_STATE_COLUMNS_ENSURED:
        return
    cursor.execute(
        """
        ALTER TABLE cross_reference_source_state
        ADD COLUMN IF NOT EXISTS catalog_hash TEXT NOT NULL DEFAULT ''
        """
    )
    cursor.execute(
        """
        ALTER TABLE cross_reference_source_state
        ADD COLUMN IF NOT EXISTS graph_sync_ok BOOLEAN NOT NULL DEFAULT false
        """
    )
    _SOURCE_STATE_COLUMNS_ENSURED = True


def create_cross_reference_tables():
    """Create cross-reference tables and indexes if they do not exist."""
    conn = None
    try:
        conn = _get_admin_connection()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS legi_reference_catalog (
                legi_doc_id TEXT PRIMARY KEY,
                parent_text_id TEXT NOT NULL,
                article_number TEXT NOT NULL,
                normalized_number TEXT NOT NULL,
                normalized_number_loose TEXT NOT NULL,
                code_family TEXT,
                code_label TEXT,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                title TEXT,
                full_title TEXT,
                aliases TEXT[] NOT NULL DEFAULT '{}',
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_legi_ref_catalog_number
                ON legi_reference_catalog (normalized_number, start_date, end_date)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_legi_ref_catalog_number_loose
                ON legi_reference_catalog (normalized_number_loose, start_date, end_date)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_legi_ref_catalog_parent_number
                ON legi_reference_catalog (parent_text_id, normalized_number)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_legi_ref_catalog_family
                ON legi_reference_catalog (code_family)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cross_reference_legi_mentions (
                mention_hash TEXT PRIMARY KEY,
                source_type TEXT NOT NULL CHECK (source_type IN ('jade', 'bofip')),
                source_doc_id TEXT NOT NULL,
                source_chunk_id TEXT NOT NULL,
                source_chunk_index INTEGER NOT NULL,
                source_date DATE NOT NULL,
                source_hash TEXT NOT NULL,
                source_title TEXT,
                source_secondary_id TEXT,
                relation_kind TEXT NOT NULL CHECK (relation_kind IN ('applies_to', 'interprets')),
                matched_text TEXT NOT NULL,
                match_start INTEGER,
                match_end INTEGER,
                normalized_number TEXT,
                normalized_number_loose TEXT,
                detected_code_alias TEXT,
                detected_code_family TEXT,
                detected_parent_text_ids TEXT[] NOT NULL DEFAULT '{}',
                target_legi_doc_id TEXT,
                target_parent_text_id TEXT,
                target_article_number TEXT,
                target_start_date DATE,
                target_end_date DATE,
                resolver_stage TEXT NOT NULL,
                resolver_method TEXT NOT NULL,
                confidence DOUBLE PRECISION NOT NULL,
                is_accepted BOOLEAN NOT NULL,
                context_window TEXT NOT NULL,
                explain JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cross_legi_mentions_source
                ON cross_reference_legi_mentions (source_type, source_doc_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cross_legi_mentions_target
                ON cross_reference_legi_mentions (target_legi_doc_id)
        """)
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cross_legi_mentions_hash
                ON cross_reference_legi_mentions (source_hash)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cross_reference_legi_edges (
                source_type TEXT NOT NULL CHECK (source_type IN ('jade', 'bofip')),
                source_doc_id TEXT NOT NULL,
                source_date DATE NOT NULL,
                source_hash TEXT NOT NULL,
                relation_kind TEXT NOT NULL CHECK (relation_kind IN ('applies_to', 'interprets')),
                target_legi_doc_id TEXT NOT NULL,
                target_parent_text_id TEXT NOT NULL,
                target_article_number TEXT NOT NULL,
                best_confidence DOUBLE PRECISION NOT NULL,
                occurrence_count INTEGER NOT NULL,
                resolver_methods TEXT[] NOT NULL DEFAULT '{}',
                source_chunk_ids TEXT[] NOT NULL DEFAULT '{}',
                normalized_numbers TEXT[] NOT NULL DEFAULT '{}',
                explain JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (source_type, source_doc_id, target_legi_doc_id)
            )
        """)

        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cross_legi_edges_target
                ON cross_reference_legi_edges (target_legi_doc_id)
        """)

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cross_reference_source_state (
                source_type TEXT NOT NULL CHECK (source_type IN ('jade', 'bofip')),
                source_doc_id TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                catalog_hash TEXT NOT NULL DEFAULT '',
                graph_sync_ok BOOLEAN NOT NULL DEFAULT false,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                PRIMARY KEY (source_type, source_doc_id)
            )
        """)
        cursor.execute(
            """
            ALTER TABLE cross_reference_source_state
            ADD COLUMN IF NOT EXISTS catalog_hash TEXT NOT NULL DEFAULT ''
            """
        )
        cursor.execute(
            """
            ALTER TABLE cross_reference_source_state
            ADD COLUMN IF NOT EXISTS graph_sync_ok BOOLEAN NOT NULL DEFAULT false
            """
        )
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_cross_source_state_hash
                ON cross_reference_source_state (source_hash)
        """)

        conn.commit()
        logger.info("Cross-reference tables ensured successfully")

    except Exception as e:
        logger.error(f"Error creating cross-reference tables: {e}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()


def refresh_legi_reference_catalog() -> str:
    """Build or rebuild legi_reference_catalog from legi table.

    Computes normalized_number, normalized_number_loose, code_family, code_label, aliases.

    Returns:
        Deterministic catalog hash representing catalog content.
    """
    from crossreference.normalizer import (
        normalize_article_number,
        loose_normalized_number,
    )
    from crossreference.alias_detector import CODE_FAMILY_MAP

    started = time.perf_counter()
    conn = None
    read_cursor = None
    try:
        conn = _get_admin_connection()
        write_cursor = conn.cursor()
        # Full rebuild so obsolete rows are removed.
        write_cursor.execute("TRUNCATE TABLE legi_reference_catalog")

        # Stream source rows in deterministic order to keep memory bounded.
        read_cursor = conn.cursor(name="legi_reference_catalog_stream")
        read_cursor.itersize = _CATALOG_STREAM_FETCH_SIZE
        read_cursor.execute(
            """
            SELECT DISTINCT ON (doc_id)
                doc_id,
                category,
                number,
                title,
                full_title,
                start_date::date,
                end_date::date
            FROM legi
            ORDER BY
                doc_id,
                (number IS NULL),
                (category IS NULL),
                (title IS NULL),
                (full_title IS NULL),
                start_date::date DESC,
                end_date::date DESC,
                category,
                number,
                title,
                full_title
            """
        )

        logger.info("Building legi_reference_catalog from LEGI stream")

        hash_builder = hashlib.sha1()
        insert_rows = []
        inserted_count = 0

        for row in read_cursor:
            doc_id, category, number, title, full_title, start_date, end_date = row
            if not number or not start_date or not end_date:
                continue

            norm_num = normalize_article_number(number)
            norm_num_loose = loose_normalized_number(norm_num)

            # Infer code_label
            code_label = None
            if title:
                t = title.lower().strip()
                if t.startswith("code ") or t.startswith("livre "):
                    code_label = title
            if not code_label and full_title:
                t = full_title.lower().strip()
                if t.startswith("code ") or t.startswith("livre "):
                    code_label = full_title

            # Infer code_family
            family = None
            if category:
                for fam_name, fam_info in CODE_FAMILY_MAP.items():
                    if category in fam_info.get("parent_text_ids", []):
                        family = fam_name
                        break

            aliases = _build_aliases_for_row(number, code_label, category)

            normalized_payload = {
                "legi_doc_id": doc_id,
                "parent_text_id": category or "",
                "article_number": number or "",
                "normalized_number": norm_num,
                "normalized_number_loose": norm_num_loose,
                "code_family": family,
                "code_label": code_label,
                "start_date": str(start_date),
                "end_date": str(end_date),
                "title": title,
                "full_title": full_title,
                "aliases": aliases,
            }
            hash_builder.update(
                json.dumps(normalized_payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
            )

            insert_rows.append(
                (
                    doc_id,
                    category or "",
                    number or "",
                    norm_num,
                    norm_num_loose,
                    family,
                    code_label,
                    start_date,
                    end_date,
                    title,
                    full_title,
                    aliases,
                )
            )

            if len(insert_rows) >= _CATALOG_INSERT_BATCH_SIZE:
                write_cursor.executemany(
                    """
                    INSERT INTO legi_reference_catalog (
                        legi_doc_id, parent_text_id, article_number,
                        normalized_number, normalized_number_loose,
                        code_family, code_label, start_date, end_date,
                        title, full_title, aliases, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                    )
                    """,
                    insert_rows,
                )
                inserted_count += len(insert_rows)
                insert_rows.clear()

        if insert_rows:
            write_cursor.executemany(
                """
                INSERT INTO legi_reference_catalog (
                    legi_doc_id, parent_text_id, article_number,
                    normalized_number, normalized_number_loose,
                    code_family, code_label, start_date, end_date,
                    title, full_title, aliases, updated_at
                ) VALUES (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW()
                )
                """,
                insert_rows,
            )
            inserted_count += len(insert_rows)

        catalog_hash = hash_builder.hexdigest()

        conn.commit()
        elapsed = time.perf_counter() - started
        logger.info(
            f"legi_reference_catalog refreshed: {inserted_count} rows in {elapsed:.2f}s (catalog_hash={catalog_hash[:12]}...)"
        )
        return catalog_hash

    except Exception as e:
        logger.error(f"Error refreshing legi_reference_catalog: {e}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if read_cursor:
            read_cursor.close()
        if conn:
            conn.close()


def _build_aliases_for_row(number, code_label, category):
    """Build alias list for a catalog row. Used for SQL-side matching."""
    from crossreference.alias_detector import CODE_FAMILY_MAP

    aliases = []

    if code_label:
        label_lower = unidecode(code_label.lower())
        aliases.append(label_lower)
        # Punctuation-stripped variant
        stripped = re.sub(r"[^\w\s]", "", label_lower).strip()
        if stripped and stripped != label_lower:
            aliases.append(stripped)

    # Known aliases from family map
    for fam_name, fam_info in CODE_FAMILY_MAP.items():
        if category in fam_info.get("parent_text_ids", []):
            aliases.extend(fam_info.get("aliases", []))

    # Deduplicate
    return list(dict.fromkeys(aliases))


def get_edge_source_hash(cursor, source_type, source_doc_id):
    """Return stored source_hash for a source doc edge, or None if never processed."""
    cursor.execute("""
        SELECT DISTINCT source_hash
        FROM cross_reference_legi_edges
        WHERE source_type = %s AND source_doc_id = %s
    """, (source_type, source_doc_id))
    rows = cursor.fetchall()
    if not rows:
        return None
    return rows[0][0]


def get_source_state(cursor, source_type, source_doc_id):
    """Return stored incremental state for one source document."""
    _ensure_source_state_columns(cursor)
    cursor.execute(
        """
        SELECT source_hash, catalog_hash, graph_sync_ok
        FROM cross_reference_source_state
        WHERE source_type = %s AND source_doc_id = %s
        """,
        (source_type, source_doc_id),
    )
    row = cursor.fetchone()
    if not row:
        return None
    return {
        "source_hash": row[0],
        "catalog_hash": row[1] or "",
        "graph_sync_ok": bool(row[2]),
    }


def get_source_state_hash(cursor, source_type, source_doc_id):
    """Backward-compatible helper returning only source_hash from state."""
    state = get_source_state(cursor, source_type, source_doc_id)
    if not state:
        return None
    return state["source_hash"]


def upsert_source_state_hash(
    cursor,
    source_type,
    source_doc_id,
    source_hash,
    catalog_hash="",
    graph_sync_ok=False,
):
    """Upsert source hash after a successful per-document rebuild."""
    _ensure_source_state_columns(cursor)
    cursor.execute(
        """
        INSERT INTO cross_reference_source_state (
            source_type, source_doc_id, source_hash, catalog_hash, graph_sync_ok, updated_at
        ) VALUES (%s, %s, %s, %s, %s, NOW())
        ON CONFLICT (source_type, source_doc_id) DO UPDATE SET
            source_hash = EXCLUDED.source_hash,
            catalog_hash = EXCLUDED.catalog_hash,
            graph_sync_ok = EXCLUDED.graph_sync_ok,
            updated_at = NOW()
        """,
        (source_type, source_doc_id, source_hash, catalog_hash, graph_sync_ok),
    )


def delete_mentions_and_edges_for_doc(cursor, source_type, source_doc_id):
    """Delete all mentions and edges for one source document before rebuild."""
    cursor.execute("""
        DELETE FROM cross_reference_legi_mentions
        WHERE source_type = %s AND source_doc_id = %s
    """, (source_type, source_doc_id))
    cursor.execute("""
        DELETE FROM cross_reference_legi_edges
        WHERE source_type = %s AND source_doc_id = %s
    """, (source_type, source_doc_id))


def insert_mentions_batch(cursor, mentions):
    """Batch insert mentions. Each mention is a dict matching cross_reference_legi_mentions columns."""
    if not mentions:
        return
    columns = [
        "mention_hash", "source_type", "source_doc_id", "source_chunk_id",
        "source_chunk_index", "source_date", "source_hash", "source_title",
        "source_secondary_id", "relation_kind", "matched_text", "match_start",
        "match_end", "normalized_number", "normalized_number_loose",
        "detected_code_alias", "detected_code_family", "detected_parent_text_ids",
        "target_legi_doc_id", "target_parent_text_id", "target_article_number",
        "target_start_date", "target_end_date", "resolver_stage", "resolver_method",
        "confidence", "is_accepted", "context_window", "explain",
    ]
    for i in range(0, len(mentions), 500):
        batch = mentions[i : i + 500]
        rows = []
        for mention in batch:
            row = [mention.get(c) for c in columns]
            # JSONB adaptation for psycopg2 execute_values
            row[-1] = psycopg2.extras.Json(row[-1] if row[-1] is not None else {})
            rows.append(tuple(row))
        psycopg2.extras.execute_values(
            cursor,
            f"""
                INSERT INTO cross_reference_legi_mentions ({", ".join(columns)})
                VALUES %s
                ON CONFLICT (mention_hash) DO UPDATE SET
                    confidence = EXCLUDED.confidence,
                    is_accepted = EXCLUDED.is_accepted,
                    target_legi_doc_id = EXCLUDED.target_legi_doc_id,
                    target_parent_text_id = EXCLUDED.target_parent_text_id,
                    target_article_number = EXCLUDED.target_article_number,
                    target_start_date = EXCLUDED.target_start_date,
                    target_end_date = EXCLUDED.target_end_date,
                    resolver_stage = EXCLUDED.resolver_stage,
                    resolver_method = EXCLUDED.resolver_method,
                    explain = EXCLUDED.explain,
                    updated_at = NOW()
            """,
            rows,
        )


def aggregate_and_upsert_edges(cursor, source_type, source_doc_id):
    """Aggregate accepted mentions into edges for one source document."""
    cursor.execute("""
        SELECT
            source_type,
            source_doc_id,
            source_date,
            source_hash,
            relation_kind,
            target_legi_doc_id,
            target_parent_text_id,
            target_article_number,
            MAX(confidence) AS best_confidence,
            COUNT(*) AS occurrence_count,
            ARRAY_AGG(DISTINCT resolver_method) AS resolver_methods,
            ARRAY_AGG(DISTINCT source_chunk_id) AS source_chunk_ids,
            ARRAY_AGG(DISTINCT normalized_number) AS normalized_numbers
        FROM cross_reference_legi_mentions
        WHERE source_type = %s
          AND source_doc_id = %s
          AND is_accepted = true
          AND target_legi_doc_id IS NOT NULL
        GROUP BY
            source_type, source_doc_id, source_date, source_hash,
            relation_kind, target_legi_doc_id, target_parent_text_id, target_article_number
    """, (source_type, source_doc_id))

    rows = cursor.fetchall()
    if not rows:
        return

    for row in rows:
        (
            src_type, src_doc, src_date, src_hash, rel_kind,
            target_id, target_parent, target_number,
            best_conf, occ_count, methods, chunk_ids, norm_nums,
        ) = row

        cursor.execute("""
            INSERT INTO cross_reference_legi_edges (
                source_type, source_doc_id, source_date, source_hash,
                relation_kind, target_legi_doc_id, target_parent_text_id,
                target_article_number, best_confidence, occurrence_count,
                resolver_methods, source_chunk_ids, normalized_numbers,
                explain, updated_at
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                '{"aggregated_from_mentions": true}'::jsonb, NOW()
            )
            ON CONFLICT (source_type, source_doc_id, target_legi_doc_id) DO UPDATE SET
                source_date = EXCLUDED.source_date,
                source_hash = EXCLUDED.source_hash,
                relation_kind = EXCLUDED.relation_kind,
                target_parent_text_id = EXCLUDED.target_parent_text_id,
                target_article_number = EXCLUDED.target_article_number,
                best_confidence = EXCLUDED.best_confidence,
                occurrence_count = EXCLUDED.occurrence_count,
                resolver_methods = EXCLUDED.resolver_methods,
                source_chunk_ids = EXCLUDED.source_chunk_ids,
                normalized_numbers = EXCLUDED.normalized_numbers,
                explain = EXCLUDED.explain,
                updated_at = NOW()
        """, (
            src_type, src_doc, src_date, src_hash, rel_kind,
            target_id, target_parent, target_number,
            best_conf, occ_count,
            sorted(methods),
            sorted(chunk_ids),
            sorted(n for n in norm_nums if n),
        ))


def _get_admin_connection():
    """Get a direct psycopg2 connection for DDL / bulk ops."""
    return psycopg2.connect(
        host=POSTGRES_HOST,
        port=POSTGRES_PORT,
        dbname=POSTGRES_DB,
        user=POSTGRES_USER,
        password=POSTGRES_PASSWORD,
    )
