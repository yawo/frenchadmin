-- Migration: Add full-text search columns to existing tables
-- Run once against existing databases to enable hybrid search (FTS + vector).
-- Safe to re-run (uses IF NOT EXISTS / IF NOT column checks).

-- LEGI
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'legi' AND column_name = 'chunk_tsv'
    ) THEN
        ALTER TABLE LEGI ADD COLUMN chunk_tsv tsvector
            GENERATED ALWAYS AS (to_tsvector('french', coalesce(chunk_text, ''))) STORED;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_legi_chunk_tsv ON LEGI USING GIN(chunk_tsv);

-- JADE
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'jade' AND column_name = 'chunk_tsv'
    ) THEN
        ALTER TABLE JADE ADD COLUMN chunk_tsv tsvector
            GENERATED ALWAYS AS (to_tsvector('french', coalesce(chunk_text, ''))) STORED;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_jade_chunk_tsv ON JADE USING GIN(chunk_tsv);

-- BOFIP
DO $$ BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'bofip' AND column_name = 'chunk_tsv'
    ) THEN
        ALTER TABLE BOFIP ADD COLUMN chunk_tsv tsvector
            GENERATED ALWAYS AS (to_tsvector('french', coalesce(chunk_text, ''))) STORED;
    END IF;
END $$;
CREATE INDEX IF NOT EXISTS idx_bofip_chunk_tsv ON BOFIP USING GIN(chunk_tsv);

ANALYZE LEGI;
ANALYZE JADE;
ANALYZE BOFIP;
