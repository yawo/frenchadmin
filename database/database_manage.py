import atexit
import json
import math
import os
import time
import uuid
from contextlib import contextmanager

import duckdb
import psycopg2
from psycopg2 import pool
from psycopg2.extras import execute_values
from config import (
    EMBEDDING_MODEL,
    ENABLE_FAST_DB_INSERT,
    FAST_DB_INSERT_PAGE_SIZE,
    POSTGRES_DB,
    POSTGRES_HOST,
    POSTGRES_PASSWORD,
    POSTGRES_PORT,
    POSTGRES_USER,
    config_file_path,
    get_logger,
    parquet_files_folder,
)
from utils import (
    _extract_distinct_data,
    format_model_name,
    format_to_table_name,
    generate_embeddings_with_retry,
)

logger = get_logger(__name__)

# Global connection pool (lazy initialization)
_connection_pool = None


def _get_pool():
    """Get or create the connection pool (lazy initialization)."""
    global _connection_pool
    if _connection_pool is None:
        _connection_pool = pool.ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
        )
        logger.info("PostgreSQL connection pool initialized")
    return _connection_pool


@contextmanager
def get_connection():
    """
    Context manager to get a connection from the pool.
    Automatically returns the connection to the pool when done.

    Usage:
        with get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(...)
            conn.commit()
    """
    conn = None
    try:
        conn = _get_pool().getconn()
        yield conn
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if conn:
            _get_pool().putconn(conn)


def close_connection_pool():
    """Close all connections in the pool."""
    global _connection_pool
    if _connection_pool is not None:
        _connection_pool.closeall()
        _connection_pool = None
        logger.info("PostgreSQL connection pool closed")


atexit.register(close_connection_pool)


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.tables
            WHERE table_name = %s
        );
    """,
        (table_name.lower(),),
    )
    return bool(cursor.fetchone()[0])


def _column_exists(cursor, table_name: str, column_name: str) -> bool:
    cursor.execute(
        """
        SELECT EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = %s AND column_name = %s
        );
    """,
        (table_name.lower(), column_name),
    )
    return bool(cursor.fetchone()[0])


def _ensure_embedding_vector_column(
    cursor,
    table_name: str,
    model_name: str,
    embedding_size: int,
):
    column_name = f"embeddings_{model_name}"
    if _column_exists(cursor, table_name, column_name):
        return
    cursor.execute(
        f'ALTER TABLE {table_name.upper()} ADD COLUMN "embeddings_{model_name}" vector({embedding_size});'
    )
    logger.info(
        "Added missing embedding column '%s' to %s",
        column_name,
        table_name.upper(),
    )


def _ensure_hnsw_index(cursor, table_name: str, model_name: str):
    cursor.execute(
        """
        SELECT indexname FROM pg_indexes
        WHERE tablename = %s AND indexdef LIKE %s;
    """,
        (table_name.lower(), f'%embeddings_{model_name}%'),
    )
    if cursor.fetchone():
        return
    cursor.execute(
        f'CREATE INDEX ON {table_name.upper()} USING hnsw ("embeddings_{model_name}" vector_cosine_ops) WITH (m = 16, ef_construction = 128);'
    )
    logger.info(
        "Created missing HNSW index on %s for embeddings_%s",
        table_name.upper(),
        model_name,
    )


def _ensure_doc_id_index(cursor, table_name: str):
    index_name = f"idx_{table_name.lower()}_doc_id"
    cursor.execute(
        f"CREATE INDEX IF NOT EXISTS {index_name} ON {table_name.upper()}(doc_id);"
    )


def _ensure_fts_column(cursor, table_name: str):
    """Add tsvector column, GIN index, and auto-update trigger for full-text search.

    Uses weighted tsvector: title gets weight A, chunk_text gets weight B.
    """
    cursor.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = 'chunk_tsv'",
        (table_name.lower(),),
    )
    if cursor.fetchone():
        return
    table_upper = table_name.upper()
    table_lower = table_name.lower()
    cursor.execute(
        f"ALTER TABLE {table_upper} ADD COLUMN IF NOT EXISTS chunk_tsv tsvector;"
    )
    cursor.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_lower}_chunk_tsv ON {table_upper} USING GIN(chunk_tsv);"
    )
    cursor.execute("""
        CREATE OR REPLACE FUNCTION tsvector_update_chunk_tsv() RETURNS trigger AS $$
        BEGIN
            NEW.chunk_tsv := setweight(to_tsvector('french', coalesce(NEW.title, '')), 'A') ||
                             setweight(to_tsvector('french', coalesce(NEW.chunk_text, '')), 'B');
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql;
    """)
    cursor.execute(f"DROP TRIGGER IF EXISTS trg_{table_lower}_chunk_tsv ON {table_upper};")
    cursor.execute(f"""
        CREATE TRIGGER trg_{table_lower}_chunk_tsv
            BEFORE INSERT OR UPDATE OF chunk_text, title ON {table_upper}
            FOR EACH ROW EXECUTE FUNCTION tsvector_update_chunk_tsv();
    """)
    logger.info("Added FTS column, GIN index, and trigger on %s", table_upper)


def _ensure_sparse_column(cursor, table_name: str):
    """Add JSONB sparse_embedding column with GIN index for sparse retrieval."""
    cursor.execute(
        "SELECT 1 FROM information_schema.columns WHERE table_name = %s AND column_name = 'sparse_embedding'",
        (table_name.lower(),),
    )
    if cursor.fetchone():
        return
    table_upper = table_name.upper()
    table_lower = table_name.lower()
    cursor.execute(
        f"ALTER TABLE {table_upper} ADD COLUMN IF NOT EXISTS sparse_embedding JSONB;"
    )
    cursor.execute(
        f"CREATE INDEX IF NOT EXISTS idx_{table_lower}_sparse_embedding ON {table_upper} USING GIN(sparse_embedding);"
    )
    logger.info("Added sparse_embedding column and GIN index on %s", table_upper)


def create_all_tables(model=EMBEDDING_MODEL, delete_existing: bool = False):
    """
    Creates the necessary tables in the PostgreSQL database as specified in the data configuration file.
    Optionally deletes existing tables before creation.
    This function:
    - Connects to the PostgreSQL database using credentials from environment variables.
    - Ensures the `pgvector` extension is enabled for vector-based operations.
    - Reads the table configuration from a JSON file.
    - Iterates through the configured table names, and for each:
        - Optionally drops the table if it exists and `delete_existing` is True.
        - Checks if the table already exists; if not, creates it with the appropriate schema.
        - Adds a vector column for embeddings and creates an HNSW index for efficient similarity search.
        - Creates an index on doc_id column for faster queries.
    - Commits all changes and logs the process.
    Args:
        model (str): The embedding model to use. Defaults to EMBEDDING_MODEL.
        delete_existing (bool, optional): If True, existing tables will be dropped before creation. Defaults to False.
    Raises:
        Logs errors if database connection, extension enabling, table creation, or index creation fails.
    """

    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
        )
        cursor = conn.cursor()
        logger.info("Connected to PostgreSQL database")
        probe_vector = generate_embeddings_with_retry(
            data="Hey, I'am a probe", model=model
        )[0]
        embedding_size = len(probe_vector)

        model_name = format_model_name(model)
        config_tables = {"table_mapping"}
        tax_tables = {"legi", "jade", "bofip"}

        try:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS vector;")
            conn.commit()

            cursor.execute("SELECT * FROM pg_extension WHERE extname = 'vector';")
            if cursor.fetchone() is None:
                logger.error(
                    "pgvector extension could not be enabled. Please check if it's installed in your PostgreSQL instance."
                )
                raise Exception("pgvector extension not enabled")
            logger.info("pgvector extension enabled successfully")
        except Exception as e:
            logger.error(f"Error enabling pgvector extension: {e}")
            raise e

        with open(config_file_path, "r") as file:
            config = json.load(file)

        configured_tax_tables = [name for name in config.keys() if name in tax_tables]
        table_names = ["table_mapping", *configured_tax_tables]

        for table_name in table_names:
            if delete_existing:
                cursor.execute(f"DROP TABLE IF EXISTS {table_name.upper()} CASCADE;")
                conn.commit()
                logger.info(
                    f"Table '{table_name.upper()}' dropped successfully in database {POSTGRES_DB}"
                )

            table_exists = _table_exists(cursor, table_name)
            if table_exists:
                logger.info(
                    f"Table '{table_name.upper()}' already exists in database {POSTGRES_DB}"
                )

            if table_name.lower() == "legi":
                if not table_exists:
                    cursor.execute(f"""
                        CREATE TABLE LEGI (
                            chunk_id TEXT PRIMARY KEY,
                            doc_id TEXT NOT NULL,
                            chunk_index INTEGER NOT NULL,
                            chunk_xxh64 TEXT NOT NULL,
                            nature TEXT,
                            category TEXT,
                            ministry TEXT,
                            status TEXT,
                            title TEXT,
                            full_title TEXT,
                            subtitles TEXT,
                            number TEXT,
                            start_date TEXT,
                            end_date TEXT,
                            nota TEXT,
                            links JSONB,
                            text TEXT,
                            chunk_text TEXT,
                            "embeddings_{model_name}" vector({embedding_size}),
                            UNIQUE(chunk_id)
                        )
                    """)
            elif table_name.lower() == "jade":
                if not table_exists:
                    cursor.execute(f"""
                        CREATE TABLE JADE (
                            chunk_id TEXT PRIMARY KEY,
                            doc_id TEXT NOT NULL,
                            chunk_index INTEGER NOT NULL,
                            chunk_xxh64 TEXT NOT NULL,
                            nature TEXT,
                            solution TEXT,
                            title TEXT,
                            number TEXT,
                            decision_date TEXT,
                            jurisdiction TEXT,
                            formation TEXT,
                            text TEXT,
                            chunk_text TEXT,
                            "embeddings_{model_name}" vector({embedding_size}),
                            UNIQUE(chunk_id)
                        )
                    """)
            elif table_name.lower() == "bofip":
                if not table_exists:
                    cursor.execute(f"""
                        CREATE TABLE BOFIP (
                            chunk_id TEXT PRIMARY KEY,
                            doc_id TEXT NOT NULL,
                            chunk_index INTEGER NOT NULL,
                            chunk_xxh64 TEXT NOT NULL,
                            title TEXT,
                            contenu_id TEXT,
                            contenu_type TEXT,
                            document_number TEXT,
                            bofip_url TEXT,
                            publication_date TEXT,
                            subjects TEXT[],
                            category_path TEXT,
                            links JSONB,
                            text TEXT,
                            chunk_text TEXT,
                            "embeddings_{model_name}" vector({embedding_size}),
                            UNIQUE(chunk_id)
                        )
                    """)
            elif table_name.lower() == "table_mapping":
                if not table_exists:
                    cursor.execute("""
                        CREATE TABLE TABLE_MAPPING (
                            table_name VARCHAR(63) PRIMARY KEY,
                            full_table_name VARCHAR NOT NULL
                        )
                    """)

            if table_name.lower() not in config_tables:
                _ensure_embedding_vector_column(
                    cursor,
                    table_name,
                    model_name,
                    embedding_size,
                )
                _ensure_hnsw_index(cursor, table_name, model_name)
                _ensure_doc_id_index(cursor, table_name)
                _ensure_fts_column(cursor, table_name)
                _ensure_sparse_column(cursor, table_name)

            conn.commit()
            logger.info(
                f"Table '{table_name.upper()}' ensured successfully in database {POSTGRES_DB}"
            )

            update_mapping_table(
                table_name=table_name[:63], full_table_name=table_name
            )

        # Wire cross-reference tables and graph schema
        try:
            from database.cross_reference_manage import create_cross_reference_tables
            create_cross_reference_tables()
        except Exception as e:
            logger.warning(f"Cross-reference tables not created: {e}")

        try:
            from database.graph_manage import init_graph_schema
            init_graph_schema()
        except Exception as e:
            logger.warning(f"Graph schema not initialized: {e}")

    except Exception as e:
        logger.error(f"Error creating tables in PostgreSQL: {e}")
        raise e
    finally:
        if conn:
            conn.close()
            logger.debug("PostgreSQL connection closed")


def update_mapping_table(table_name: str, full_table_name: str):
    """
    Inserts or updates a mapping entry in the TABLE_MAPPING table.

    Args:
        table_name (str): The short name of the table (max 63 characters by default in PostgreSQL).
        full_table_name (str): The full name of the table.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
        )
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO TABLE_MAPPING (table_name, full_table_name)
            VALUES (%s, %s)
            ON CONFLICT (table_name) DO UPDATE SET
                full_table_name = EXCLUDED.full_table_name;
        """,
            (table_name, full_table_name),
        )
        conn.commit()
        logger.debug(f"Inserted/Updated mapping: '{table_name}' -> '{full_table_name}'")
    except Exception as e:
        logger.error(f"Error inserting/updating mapping table: {e}")
    finally:
        if conn:
            conn.close()
            logger.debug("PostgreSQL connection closed")


@contextmanager
def refresh_table(table_name: str, model: str = EMBEDDING_MODEL):
    """
    Context manager for refreshing a PostgreSQL table by dropping indexes, truncating data,
    and recreating indexes.

    Args:
        table_name (str): Name of the table to refresh
        model (str): Embedding model name for index recreation
    """
    conn = None
    try:
        # Drop indexes + Truncate
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
        )
        cursor = conn.cursor()
        model_name = format_model_name(model)

        # Check if table exists
        cursor.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_name = %s
            );
        """,
            (table_name.lower(),),
        )

        if not cursor.fetchone()[0]:
            logger.warning(f"Table '{table_name.upper()}' does not exist")
            yield
            return

        logger.info(f"Starting refresh for table {table_name.upper()}")

        # Drop HNSW index
        cursor.execute(
            """
            SELECT indexname FROM pg_indexes
            WHERE tablename = %s AND indexdef LIKE %s;
        """,
            (table_name.lower(), f"%embeddings_{model_name}%"),
        )

        hnsw_result = cursor.fetchone()
        if hnsw_result:
            cursor.execute(f'DROP INDEX IF EXISTS "{hnsw_result[0]}";')
            logger.debug(f"Dropped HNSW index on {table_name.upper()}")

        # Drop doc_id index
        doc_id_idx = f"idx_{table_name.lower()}_doc_id"
        cursor.execute(f"DROP INDEX IF EXISTS {doc_id_idx};")
        logger.debug(f"Dropped B-tree index on doc_id for {table_name.upper()}")

        # Truncate table
        cursor.execute(f"TRUNCATE TABLE {table_name.upper()} RESTART IDENTITY;")
        conn.commit()
        logger.info(f"Table {table_name.upper()} truncated successfully")

        conn.close()

        yield

        # Recreate indexes
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
        )
        cursor = conn.cursor()

        logger.info(f"Recreating indexes on {table_name.upper()}...")

        # Recreate HNSW index
        cursor.execute(
            f"""CREATE INDEX ON {table_name.upper()} USING hnsw ("embeddings_{model_name}" vector_cosine_ops) WITH (m = 16, ef_construction = 128);"""
        )
        logger.debug(f"HNSW index recreated on {table_name.upper()}")

        # Recreate doc_id index
        cursor.execute(
            f"""CREATE INDEX idx_{table_name.lower()}_doc_id ON {table_name.upper()}(doc_id);"""
        )
        logger.debug(f"B-tree index on doc_id recreated for {table_name.upper()}")

        # Update statistics
        cursor.execute(f"ANALYZE {table_name.upper()};")
        conn.commit()
        logger.debug(
            f"All indexes recreated and statistics updated for {table_name.upper()}"
        )

    except Exception as e:
        logger.error(f"Error during table refresh for '{table_name.upper()}': {e}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()
            logger.debug("PostgreSQL connection closed")


def drop_table(table_name: str, cascade: bool = False):
    """
    Drop a PostgreSQL table.

    Args:
        table_name (str): Name of the table to drop
        cascade (bool): If True, automatically drop objects that depend on the table.
                       Defaults to False.

    Raises:
        Logs errors if any exception occurs during database operations.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
        )
        cursor = conn.cursor()

        # Check if table exists
        cursor.execute(f"""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = '{table_name.lower()}'
            );
        """)

        if not cursor.fetchone()[0]:
            logger.warning(f"Table '{table_name.upper()}' does not exist")
            return

        # Drop the table
        cascade_clause = "CASCADE" if cascade else ""
        cursor.execute(f"DROP TABLE {table_name.upper()} {cascade_clause};")

        conn.commit()
        logger.info(
            f"Table '{table_name.upper()}' dropped successfully from database {POSTGRES_DB}"
        )

    except Exception as e:
        logger.error(f"Error dropping table '{table_name.upper()}': {e}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()
            logger.debug("PostgreSQL connection closed")


def create_table_from_existing(
    source_table: str, target_table: str, include_indexes: bool = True
):
    """
    Copy the structure of a PostgreSQL table without its data.

    Args:
        source_table (str): Name of the source table to copy from
        target_table (str): Name of the new table to create
        include_indexes (bool): Whether to include indexes and constraints

    Raises:
        Logs errors if any exception occurs during database operations.
    """
    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
        )
        cursor = conn.cursor()

        # Check if source table exists
        cursor.execute(f"""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = '{source_table.lower()}'
            );
        """)

        if not cursor.fetchone()[0]:
            logger.error(f"Source table '{source_table.upper()}' does not exist")
            return

        # Check if target table already exists
        cursor.execute(f"""
            SELECT EXISTS (
                SELECT 1
                FROM information_schema.tables
                WHERE table_name = '{target_table.lower()}'
            );
        """)

        if cursor.fetchone()[0]:
            logger.info(f"Target table '{target_table.upper()}' already exists")
            return

        if include_indexes:
            # Copy structure with all constraints and indexes
            cursor.execute(f"""
                CREATE TABLE {target_table.upper()} 
                (LIKE {source_table.upper()} INCLUDING ALL);
            """)
        else:
            # Copy only column structure
            cursor.execute(f"""
                CREATE TABLE {target_table.upper()} 
                (LIKE {source_table.upper()} INCLUDING DEFAULTS INCLUDING CONSTRAINTS);
            """)

        # Register the table name mapping
        update_mapping_table(
            table_name=target_table.lower()[:63], full_table_name=target_table.lower()
        )  # Truncate to 63 chars for PostgreSQL table name limit

        conn.commit()
        logger.info(
            f"Table structure successfully copied from '{source_table.upper()}' to '{target_table.upper()}'"
        )
        logger.debug(f"Registered mapping: '{target_table}' -> '{target_table}'")

    except Exception as e:
        logger.error(f"Error copying table structure: {e}")
    finally:
        if conn:
            conn.close()
            logger.debug("PostgreSQL connection closed")


def _split_table(
    source_table: str,
    target_table: str,
    data_type: str,
    value: str,
    batch_size: int = 50000,
):
    """
    Split data from source table to target table based on specified criteria.

    E.g., insert data from a source table into a target table based on a specific LEGI category or code.

    Args:
        source_table (str): Name of the source table to query from
        target_table (str): Name of the target table to insert data into
        data_type (str): Type of filter to apply ('category' or 'code')
        value (str): Value to filter by (category name or code title)
        batch_size (int): Number of rows to process per batch. Default is 50,000.

    Returns:
        None: Prints success/error messages to logs
    """
    conn = None
    cursor = None
    insert_cursor = None

    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
        )

        # Building WHERE clause based on data_type
        if data_type == "category":
            where_clause = f"LOWER(category) = '{value.lower()}'"
        elif data_type == "code" and source_table.lower() == "legi":
            escaped_value = value.lower().replace("'", "''")
            where_clause = f"LOWER(category) = 'code' AND LOWER(unaccent(full_title)) LIKE LOWER(unaccent('%{escaped_value}%'))"
        else:
            logger.error(f"Invalid type '{data_type}' specified.")
            return

        # Check first if there is data to copy
        check_cursor = conn.cursor()
        check_query = f"""
            SELECT COUNT(*) FROM {source_table.upper()}
            WHERE {where_clause}
        """
        check_cursor.execute(check_query)
        row_count = check_cursor.fetchone()[0]
        check_cursor.close()

        if row_count == 0:
            logger.warning(
                f"No data found for {data_type} '{value}' in table '{source_table.upper()}'"
            )
            return

        logger.info(f"Found {row_count:,} rows to copy for {data_type} '{value}'")

        # Retrieve column names BEFORE creating the named cursor
        metadata_cursor = conn.cursor()
        metadata_cursor.execute(f"""
            SELECT column_name 
            FROM information_schema.columns 
            WHERE table_name = '{source_table.lower()}'
            ORDER BY ordinal_position
        """)
        columns = [row[0] for row in metadata_cursor.fetchall()]
        metadata_cursor.close()

        escaped_columns = [f'"{col}"' for col in columns]

        # Create a named cursor (server-side cursor)
        cursor = conn.cursor(name=f"split_cursor_{uuid.uuid4().hex[:8]}")
        cursor.itersize = batch_size

        # SELECT query with ORDER BY for consistency
        select_query = f"""
            SELECT * FROM {source_table.upper()}
            WHERE {where_clause}
            ORDER BY chunk_id
        """

        cursor.execute(select_query)

        # Create a second cursor for INSERT operations
        insert_cursor = conn.cursor()

        # Retrieve JSONB columns ONCE
        insert_cursor.execute(f"""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = '{target_table.lower()}'
            AND data_type = 'jsonb';
        """)
        jsonb_columns = {row[0] for row in insert_cursor.fetchall()}
        jsonb_indices = {i for i, col in enumerate(columns) if col in jsonb_columns}

        # Prepare INSERT query
        placeholders = ", ".join(["%s"] * len(columns))
        update_clause = ", ".join(
            [f'"{col}" = EXCLUDED."{col}"' for col in columns if col != "chunk_id"]
        )
        insert_query = f"""
            INSERT INTO {target_table.upper()} ({", ".join(escaped_columns)})
            VALUES ({placeholders})
            ON CONFLICT (chunk_id) DO UPDATE SET {update_clause};
        """
        processed_rows = 0
        batch_number = 0

        # Processing batches
        while True:
            batch_number += 1

            rows = cursor.fetchmany(batch_size)
            if not rows:
                break

            # Convert JSONB columns
            converted_rows = []
            for row in rows:
                converted_row = list(row)
                for idx in jsonb_indices:
                    if converted_row[idx] is not None and isinstance(
                        converted_row[idx], (dict, list)
                    ):
                        converted_row[idx] = json.dumps(converted_row[idx])
                converted_rows.append(tuple(converted_row))

            # Execute the insertion
            insert_cursor.executemany(insert_query, converted_rows)
            processed_rows += len(rows)

            if batch_number % 10 == 0:  # Log every 10 batches
                logger.info(f"Batch {batch_number}: {processed_rows:,} rows processed")

        conn.commit()

        logger.info(
            f"Data successfully inserted into table '{target_table.upper()}' "
            f"for {data_type} '{value.upper()}' ({processed_rows:,} rows in {batch_number} batches)"
        )

    except Exception as e:
        logger.error(f"Error splitting table data for {data_type} '{value}': {e}")
        if conn:
            conn.rollback()
        raise e
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if insert_cursor:
            try:
                insert_cursor.close()
            except Exception:
                pass
        if conn:
            conn.close()
            logger.debug("PostgreSQL connection closed")


def split_legi_table(source_table: str = "legi", export_to_parquet: bool = False):
    """
    Split the main legi table into separate tables based on codes and categories.

    Creates individual tables for each legal code and category, copying structure
    and data from the source legi table while maintaining indexes.

    Args:
        source_table (str): Name of the source legi table. Defaults to "legi".
        export_to_parquet (bool): If True, exports the created tables to Parquet files
                                  and drops them from the database. Defaults to False.
    """
    legi_codes = _extract_distinct_data(data_type="codes", source_table=source_table)
    legi_categories = _extract_distinct_data(
        data_type="category", source_table=source_table
    )

    # Remove 'CODE' as it is already handled separately
    if "CODE" in legi_categories:
        legi_categories.remove("CODE")

    def _process_legi_split(items: list, data_type: str):
        """
        Helper to process codes or categories uniformly.

        Args:
            items (list): List of codes or categories to process.
            data_type (str): Type of data being processed ('code' or 'category').
        """
        for item in sorted(items):
            if not item:
                if data_type == "category":
                    not_item = "uncategorized"
                else:
                    not_item = "misc"
                target_table = f"{source_table.lower()}_{format_to_table_name(not_item)}"  # Full table name
            else:
                target_table = f"{source_table.lower()}_{format_to_table_name(item)}"  # Full table name

            truncated_target_table = target_table[:63]  # Truncated for PostgreSQL limit

            create_table_from_existing(
                source_table=source_table,
                target_table=target_table,  # Automatically truncated to 63 chars by PostgreSQL (table name limit)
                include_indexes=True,
            )

            _split_table(
                source_table=source_table,
                target_table=truncated_target_table,  # Truncate to 63 chars for PostgreSQL table name limit
                data_type=data_type,
                value=item,
            )

            if export_to_parquet:
                try:
                    export_table_to_parquet(table_name=truncated_target_table)
                    drop_table(table_name=truncated_target_table)

                except Exception as e:
                    logger.error(
                        f"Failed to export table {target_table} to Parquet: {e}"
                    )

    _process_legi_split(items=legi_codes, data_type="code")
    _process_legi_split(items=legi_categories, data_type="category")


def export_table_to_parquet(
    table_name: str,
    parquet_folder: str = parquet_files_folder,
    rows_per_file: int = 50000,
):
    """
    Exports tables from the PostgreSQL database to Parquet files.
    Groups rows by doc_id to ensure all chunks of the same document stay together.

    Args:
        table_name (str): The name of the table to export, or "all" for all tables.
        parquet_folder (str): The path where the Parquet files will be saved.
        rows_per_file (int): Target number of rows per file. Defaults to 50000.
                            Actual count may vary to keep doc_id groups intact.

    Returns:
        None
    """
    try:
        conn = duckdb.connect()
        conn.execute("INSTALL postgres")
        conn.execute("LOAD postgres")

        conn.execute(f"""
            ATTACH 'postgresql://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}' 
            AS postgres_db (TYPE postgres)
        """)

        def _export_single_table(table_name: str, folder_name: str = ""):
            """Exports a single table, grouping by doc_id into multiple Parquet files."""
            try:
                # Count total rows in the table
                conn.execute(f"SELECT COUNT(*) FROM postgres_db.{table_name}")
                table_row_count = conn.fetchone()[0]

                if table_row_count == 0:
                    logger.warning(f"No data found in table '{table_name}', skipping.")
                    return

                # Retrieve all doc_id with their chunk count, sorted
                conn.execute(f"""
                    SELECT doc_id, COUNT(*) as chunk_count 
                    FROM postgres_db.{table_name} 
                    GROUP BY doc_id 
                    ORDER BY doc_id
                """)
                doc_id_counts = conn.fetchall()

                total_doc_ids = len(doc_id_counts)
                logger.info(
                    f"Exporting {table_row_count} rows from table '{table_name}' "
                    f"({total_doc_ids} distinct doc_ids)..."
                )

                # Reading table_mapping
                conn.execute(f"""SELECT full_table_name FROM postgres_db.table_mapping
                                 WHERE table_name = '{table_name}';""")
                full_table_name = conn.fetchone()[0]

                full_output_folder = os.path.join(parquet_folder, folder_name)
                os.makedirs(full_output_folder, exist_ok=True)

                file_index = 0
                current_batch_doc_ids = []
                current_row_count = 0

                has_chunk_index = (
                    conn.execute(
                        f"""SELECT COUNT(*) FROM information_schema.columns 
                        WHERE table_schema = 'public' 
                        AND LOWER(table_name) = LOWER('{table_name}') 
                        AND column_name = 'chunk_index'"""
                    ).fetchone()[0]
                    > 0
                )

                for doc_id, chunk_count in doc_id_counts:
                    # If adding this doc_id exceeds the limit AND we already have doc_ids
                    if current_batch_doc_ids and (
                        current_row_count + chunk_count > rows_per_file
                    ):
                        # Export the current batch
                        _export_batch(
                            conn=conn,
                            table_name=table_name,
                            full_table_name=full_table_name,
                            doc_ids=current_batch_doc_ids,
                            file_index=file_index,
                            output_folder=full_output_folder,
                            row_count=current_row_count,
                            has_chunk_index=has_chunk_index,
                        )
                        file_index += 1
                        current_batch_doc_ids = []
                        current_row_count = 0

                    # Add this doc_id to the current batch
                    current_batch_doc_ids.append(doc_id)
                    current_row_count += chunk_count

                # Export the last batch if there are remaining doc_ids
                if current_batch_doc_ids:
                    _export_batch(
                        conn=conn,
                        table_name=table_name,
                        full_table_name=full_table_name,
                        doc_ids=current_batch_doc_ids,
                        file_index=file_index,
                        output_folder=full_output_folder,
                        row_count=current_row_count,
                        has_chunk_index=has_chunk_index,
                    )
                    file_index += 1

                # Check the total number of rows exported
                global_path = os.path.join(
                    full_output_folder,
                    full_table_name,
                    f"{full_table_name}_part_*.parquet",
                )
                parquet_row_count = conn.execute(
                    f"SELECT COUNT(*) FROM read_parquet('{global_path}')"
                ).fetchone()[0]

                logger.info(
                    f"Successfully exported table '{table_name}': "
                    f"{table_row_count} rows -> {parquet_row_count} rows in {file_index} file(s)."
                )

            except Exception as table_error:
                logger.error(f"Error processing table '{table_name}': {table_error}")
                if table_name != "all":
                    raise

        def _export_batch(
            conn,
            table_name: str,
            full_table_name: str,
            doc_ids: list,
            file_index: int,
            output_folder: str,
            row_count: int,
            has_chunk_index: bool,
        ):
            """
            Exports a batch of doc_ids to a single Parquet file.

            Args:
                conn: DuckDB connection object.
                table_name (str): Name of the table to export from.
                full_table_name (str): Full table name for output folder naming.
                doc_ids (list): List of doc_ids to include in this batch.
                file_index (int): Index of the output file.
                output_folder (str): Base output folder path.
                row_count (int): Number of rows in this batch.
                has_chunk_index (bool): Whether the table has a chunk_index column.
            """
            try:
                final_output_folder = os.path.join(output_folder, full_table_name)
                os.makedirs(final_output_folder, exist_ok=True)
                output_path = os.path.join(
                    final_output_folder,
                    f"{full_table_name}_part_{file_index}.parquet",
                )

                # Creating WHERE clause
                doc_ids_escaped = [doc_id.replace("'", "''") for doc_id in doc_ids]
                doc_ids_str = "', '".join(doc_ids_escaped)

                logger.debug(
                    f"Exporting part {file_index}: {len(doc_ids)} doc_ids, "
                    f"~{row_count} rows to {output_path}"
                )

                if has_chunk_index:
                    conn.execute(f"""
                        COPY (
                            SELECT * FROM postgres_db.{table_name}
                            WHERE doc_id IN ('{doc_ids_str}')
                            ORDER BY doc_id, chunk_index
                        ) TO '{output_path}'
                        (FORMAT PARQUET, COMPRESSION 'ZSTD', PARQUET_VERSION 'V2', ROW_GROUP_SIZE 50000)
                    """)
                else:
                    conn.execute(f"""
                    COPY (
                        SELECT * FROM postgres_db.{table_name}
                        WHERE doc_id IN ('{doc_ids_str}')
                        ORDER BY doc_id
                    ) TO '{output_path}'
                    (FORMAT PARQUET, COMPRESSION 'ZSTD', PARQUET_VERSION 'V2', ROW_GROUP_SIZE 50000)
                """)

                logger.debug(
                    f"Successfully exported batch {file_index} to {output_path}"
                )

            except Exception as e:
                logger.error(
                    f"Error exporting batch {file_index} for table '{table_name}': {e}"
                )
                logger.error(
                    f"Failed doc_ids: {doc_ids[:5]}..."
                )  # Log first 5 doc_ids for debugging
                raise

        os.makedirs(parquet_folder, exist_ok=True)

        if table_name == "all":
            conn.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema='public' AND table_type='BASE TABLE';"
            )
            tables = [row[0] for row in conn.fetchall()]
            logger.info(f"Found {len(tables)} tables to export: {tables}")
            if "table_mapping" in tables:
                tables.remove("table_mapping")  # Exclude table_mapping table
            for table in tables:
                _export_single_table(table_name=table)
        elif table_name.startswith("legi_"):
            _export_single_table(table_name=table_name, folder_name="legi")
        else:
            _export_single_table(table_name=table_name)

    except Exception as e:
        logger.error(f"An error occurred during SQL Table export: {e}")
        raise
    finally:
        if "conn" in locals():
            conn.close()


def _sanitize_embedding(embedding):
    """Validate embedding vector and reject non-finite values."""
    if embedding is None:
        return None
    sanitized = []
    for value in embedding:
        try:
            float_value = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"Invalid embedding value type: {type(value)}"
            ) from exc
        if not math.isfinite(float_value):
            raise ValueError("Non-finite embedding value detected (NaN/Inf).")
        sanitized.append(float_value)
    return sanitized


def _sanitize_row(row):
    """Clean row data: replace NaN in last column (embedding) with 0.0."""
    if not row or len(row) == 0:
        return row
    row_list = list(row)
    last_val = row_list[-1]
    if isinstance(last_val, (list, tuple)):
        row_list[-1] = _sanitize_embedding(last_val)
    return tuple(row_list)


def insert_data(data: list, table_name: str, model=EMBEDDING_MODEL):
    """
    Inserts a list of data rows into the specified PostgreSQL table, handling upserts and duplicate avoidance.

    Depending on the table name, constructs the appropriate INSERT ... ON CONFLICT SQL statement and executes it for all provided data rows.
    Existing rows with the same 'doc_id' are deleted before insertion to avoid duplicates and outdated data.

    Args:
        data (list): A list of tuples, each representing a row to insert into the database.
        table_name (str): The name of the target table. Supported values are "legi", "jade", and "bofip".

    Raises:
        Logs errors if any exception occurs during database operations.

    Notes:
        - Uses psycopg2 for PostgreSQL connection and execution.
        - Table and column names are hardcoded for each supported table.
        - Performs upsert (insert or update on conflict) based on the primary key 'chunk_id'.
        - Logs an error and returns if an unknown table name is provided.
    """
    if not data:
        return

    data = [_sanitize_row(row) for row in data]

    with get_connection() as conn:
        cursor = conn.cursor()
        started_at = time.perf_counter()

        model_name = format_model_name(model)
        table_lower = table_name.lower()
        if table_lower not in {"legi", "jade", "bofip"}:
            logger.error(
                "Unsupported table in tax-only mode: %s (expected one of: legi, jade, bofip)",
                table_name,
            )
            return

        source_doc_ids = sorted({row[1] for row in data if len(row) > 1 and row[1]})
        if source_doc_ids:
            delete_query = f"DELETE FROM {table_name.upper()} WHERE doc_id = ANY(%s)"
            cursor.execute(delete_query, (source_doc_ids,))

        def _execute_fast_insert(
            target_table: str,
            columns: list[str],
            update_columns: list[str],
            rows: list,
        ):
            column_list = ", ".join(columns)
            update_clause = ", ".join(
                [f"{col} = EXCLUDED.{col}" for col in update_columns]
            )
            query = f"""
                INSERT INTO {target_table} ({column_list})
                VALUES %s
                ON CONFLICT (chunk_id) DO UPDATE SET
                {update_clause};
            """
            execute_values(cursor, query, rows, page_size=FAST_DB_INSERT_PAGE_SIZE)

        fast_insert_done = False
        if ENABLE_FAST_DB_INSERT and table_lower == "legi":
            emb_col = f'"embeddings_{model_name}"'
            columns = [
                "chunk_id", "doc_id", "chunk_index", "chunk_xxh64", "nature", "category", "ministry",
                "status", "title", "full_title", "subtitles", "number", "start_date", "end_date", "nota",
                "links", "text", "chunk_text", emb_col,
            ]
            update_columns = [c for c in columns if c != "chunk_id"]
            _execute_fast_insert("LEGI", columns, update_columns, data)
            fast_insert_done = True
        elif ENABLE_FAST_DB_INSERT and table_lower == "jade":
            emb_col = f'"embeddings_{model_name}"'
            columns = [
                "chunk_id", "doc_id", "chunk_index", "chunk_xxh64", "nature", "solution", "title", "number",
                "decision_date", "jurisdiction", "formation", "text", "chunk_text", emb_col,
            ]
            update_columns = [c for c in columns if c != "chunk_id"]
            _execute_fast_insert("JADE", columns, update_columns, data)
            fast_insert_done = True
        elif ENABLE_FAST_DB_INSERT and table_lower == "bofip":
            emb_col = f'"embeddings_{model_name}"'
            columns = [
                "chunk_id", "doc_id", "chunk_index", "chunk_xxh64", "title", "contenu_id", "contenu_type",
                "document_number", "bofip_url", "publication_date", "subjects", "category_path", "links", "text",
                "chunk_text", emb_col,
            ]
            update_columns = [c for c in columns if c != "chunk_id"]
            _execute_fast_insert("BOFIP", columns, update_columns, data)
            fast_insert_done = True

        if table_lower == "legi":
            insert_query = f"""
                INSERT INTO LEGI (chunk_id, doc_id, chunk_index, chunk_xxh64, nature, category, ministry, status, title, full_title, subtitles, number, start_date, end_date, nota, links, text, chunk_text, "embeddings_{model_name}")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chunk_id) DO UPDATE SET
                doc_id = EXCLUDED.doc_id,
                chunk_index = EXCLUDED.chunk_index,
                chunk_xxh64 = EXCLUDED.chunk_xxh64,
                nature = EXCLUDED.nature,
                category = EXCLUDED.category,
                ministry = EXCLUDED.ministry,
                status = EXCLUDED.status,
                title = EXCLUDED.title,
                full_title = EXCLUDED.full_title,
                subtitles = EXCLUDED.subtitles,
                number = EXCLUDED.number,
                start_date = EXCLUDED.start_date,
                end_date = EXCLUDED.end_date,
                nota = EXCLUDED.nota,
                links = EXCLUDED.links,
                text = EXCLUDED.text,
                chunk_text = EXCLUDED.chunk_text,
                "embeddings_{model_name}" = EXCLUDED."embeddings_{model_name}";
            """
        elif table_lower == "jade":
            insert_query = f"""
                INSERT INTO JADE (chunk_id, doc_id, chunk_index, chunk_xxh64, nature, solution, title, number, decision_date, jurisdiction, formation, text, chunk_text, "embeddings_{model_name}")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chunk_id) DO UPDATE SET
                doc_id = EXCLUDED.doc_id,
                chunk_index = EXCLUDED.chunk_index,
                chunk_xxh64 = EXCLUDED.chunk_xxh64,
                nature = EXCLUDED.nature,
                solution = EXCLUDED.solution,
                title = EXCLUDED.title,
                number = EXCLUDED.number,
                decision_date = EXCLUDED.decision_date,
                jurisdiction = EXCLUDED.jurisdiction,
                formation = EXCLUDED.formation,
                text = EXCLUDED.text,
                chunk_text = EXCLUDED.chunk_text,
                "embeddings_{model_name}" = EXCLUDED."embeddings_{model_name}";
            """
        else:
            insert_query = f"""
                INSERT INTO BOFIP (chunk_id, doc_id, chunk_index, chunk_xxh64, title, contenu_id, contenu_type, document_number, bofip_url, publication_date, subjects, category_path, links, text, chunk_text, "embeddings_{model_name}")
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (chunk_id) DO UPDATE SET
                doc_id = EXCLUDED.doc_id,
                chunk_index = EXCLUDED.chunk_index,
                chunk_xxh64 = EXCLUDED.chunk_xxh64,
                title = EXCLUDED.title,
                contenu_id = EXCLUDED.contenu_id,
                contenu_type = EXCLUDED.contenu_type,
                document_number = EXCLUDED.document_number,
                bofip_url = EXCLUDED.bofip_url,
                publication_date = EXCLUDED.publication_date,
                subjects = EXCLUDED.subjects,
                category_path = EXCLUDED.category_path,
                links = EXCLUDED.links,
                text = EXCLUDED.text,
                chunk_text = EXCLUDED.chunk_text,
                "embeddings_{model_name}" = EXCLUDED."embeddings_{model_name}";
            """

        try:
            if not fast_insert_done:
                cursor.executemany(insert_query, data)
            conn.commit()
            elapsed = time.perf_counter() - started_at
            logger.info(
                "DB insert stage completed: table=%s rows=%s mode=%s commit_sec=%.3f",
                table_name.upper(),
                len(data),
                "execute_values" if fast_insert_done else "executemany",
                elapsed,
            )
        except Exception as e:
            logger.error(
                f"Error inserting data into PostgreSQL: {e}\n{str(data)[:200]}..."
            )
            raise e


def remove_data(table_name: str, column: str, value: str):
    """
    Remove data from a PostgreSQL table based on a specific column and value.

    Args:
        table_name (str): Name of the PostgreSQL table to remove data from.
        column (str): Column name to filter the rows to be removed.
        value (str): Value in the specified column to match for removal.

    Raises:
        Exception: Any error encountered during database operations is logged.
    """
    try:
        with get_connection() as conn:
            cursor = conn.cursor()
            delete_query = f"DELETE FROM {table_name.upper()} WHERE {column} = %s"
            cursor.execute(delete_query, (value,))
            conn.commit()
            logger.info(
                f"Data removed from {table_name.upper()} table where {column} = {value} (if exists)"
            )
    except Exception as e:
        logger.error(f"Error removing data from PostgreSQL: {e}")


def get_distinct_values(table_name: str, column: str) -> list:
    """
    Retrieves all unique values from a specified column in a PostgreSQL table.

    Args:
        table_name (str): The name of the table to query.
        column (str): The name of the column to retrieve distinct values from.

    Returns:
        list: A list containing unique values from the specified column, or an empty list if the table is empty.
    """
    conn = None
    all_values = []
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
        )
        cursor = conn.cursor()

        logger.info(
            f"Fetching existing values from column {column} in table {table_name.upper()}..."
        )
        cursor.execute(f"SELECT DISTINCT {column} FROM {table_name.upper()};")

        all_values = [row[0] for row in cursor.fetchall()]

    except Exception as e:
        logger.error(f"Error connecting to the database: {e}")
        raise e
    finally:
        if conn:
            conn.close()
            logger.debug("Database connection closed.")
        return all_values


def sync_obsolete_doc_ids(table_name: str, old_doc_ids: list, new_doc_ids: list):
    """
    Synchronizes a table by deleting rows with obsolete document ids.

    This function compares the provided lists of old_doc_ids and new_doc_ids against all existing document ids in the table.
    Any document id present in the table but not in the new list is considered obsolete and all its corresponding
    rows are deleted in a single, efficient operation.

    Args:
        table_name (str): The name of the table to synchronize.
        old_doc_ids (list): A list of all existing document ids in the table.
        new_doc_ids (list): A list of all current, valid document ids.
    """
    if not new_doc_ids or not old_doc_ids:
        logger.warning(
            f"Received an empty list of new or old document ids for table {table_name}. Skipping deletion."
        )
        return

    conn = None
    try:
        conn = psycopg2.connect(
            host=POSTGRES_HOST,
            port=POSTGRES_PORT,
            dbname=POSTGRES_DB,
            user=POSTGRES_USER,
            password=POSTGRES_PASSWORD,
        )
        cursor = conn.cursor()

        logger.info(
            f"Fetching existing document ids from table {table_name.upper()}..."
        )

        old_doc_ids_set = set(old_doc_ids)
        logger.debug(f"Found {len(old_doc_ids_set)} unique existing document ids.")

        # Comparing old and new document ids to find obsolete ones
        new_doc_ids_set = set(new_doc_ids)
        logger.debug(f"Received {len(new_doc_ids_set)} new document ids.")

        doc_ids_to_delete = old_doc_ids_set - new_doc_ids_set

        # Delete all obsolete document ids in a single query
        if doc_ids_to_delete:
            logger.info(
                f"Found {len(doc_ids_to_delete)} obsolete document ids to delete."
            )
            delete_query = f"DELETE FROM {table_name.upper()} WHERE doc_id IN %s;"
            cursor.execute(delete_query, (tuple(doc_ids_to_delete),))
            conn.commit()
            logger.info(
                f"Successfully deleted {cursor.rowcount} rows for {len(doc_ids_to_delete)} obsolete document ids from {table_name.upper()}."
            )
        else:
            logger.info(
                f"No obsolete document ids found in {table_name.upper()}. No deletion needed."
            )

    except Exception as e:
        logger.error(
            f"Error during obsolete document id synchronization for table {table_name}: {e}"
        )
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()
            logger.debug("PostgreSQL connection closed")
