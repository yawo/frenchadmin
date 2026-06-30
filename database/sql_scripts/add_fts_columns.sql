-- Migration: Add full-text search columns to existing tables
-- Uses a plain column + trigger (not GENERATED STORED) to avoid full-table rewrite OOM.
-- Backfills in batches of 5000 rows to stay within shared memory limits.
-- Safe to re-run (idempotent).

-- Helper function shared by all triggers
CREATE OR REPLACE FUNCTION tsvector_update_chunk_tsv() RETURNS trigger AS $$
BEGIN
    NEW.chunk_tsv := to_tsvector('french', coalesce(NEW.chunk_text, ''));
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ============ LEGI ============
ALTER TABLE LEGI ADD COLUMN IF NOT EXISTS chunk_tsv tsvector;
CREATE INDEX IF NOT EXISTS idx_legi_chunk_tsv ON LEGI USING GIN(chunk_tsv);

DROP TRIGGER IF EXISTS trg_legi_chunk_tsv ON LEGI;
CREATE TRIGGER trg_legi_chunk_tsv
    BEFORE INSERT OR UPDATE OF chunk_text ON LEGI
    FOR EACH ROW EXECUTE FUNCTION tsvector_update_chunk_tsv();

DO $$
DECLARE
    batch_size INT := 5000;
    updated INT;
BEGIN
    LOOP
        UPDATE LEGI SET chunk_tsv = to_tsvector('french', coalesce(chunk_text, ''))
        WHERE chunk_id IN (
            SELECT chunk_id FROM LEGI WHERE chunk_tsv IS NULL LIMIT batch_size
        );
        GET DIAGNOSTICS updated = ROW_COUNT;
        EXIT WHEN updated = 0;
        RAISE NOTICE 'LEGI: backfilled % rows', updated;
    END LOOP;
END $$;

-- ============ JADE ============
ALTER TABLE JADE ADD COLUMN IF NOT EXISTS chunk_tsv tsvector;
CREATE INDEX IF NOT EXISTS idx_jade_chunk_tsv ON JADE USING GIN(chunk_tsv);

DROP TRIGGER IF EXISTS trg_jade_chunk_tsv ON JADE;
CREATE TRIGGER trg_jade_chunk_tsv
    BEFORE INSERT OR UPDATE OF chunk_text ON JADE
    FOR EACH ROW EXECUTE FUNCTION tsvector_update_chunk_tsv();

DO $$
DECLARE
    batch_size INT := 5000;
    updated INT;
BEGIN
    LOOP
        UPDATE JADE SET chunk_tsv = to_tsvector('french', coalesce(chunk_text, ''))
        WHERE chunk_id IN (
            SELECT chunk_id FROM JADE WHERE chunk_tsv IS NULL LIMIT batch_size
        );
        GET DIAGNOSTICS updated = ROW_COUNT;
        EXIT WHEN updated = 0;
        RAISE NOTICE 'JADE: backfilled % rows', updated;
    END LOOP;
END $$;

-- ============ BOFIP ============
ALTER TABLE BOFIP ADD COLUMN IF NOT EXISTS chunk_tsv tsvector;
CREATE INDEX IF NOT EXISTS idx_bofip_chunk_tsv ON BOFIP USING GIN(chunk_tsv);

DROP TRIGGER IF EXISTS trg_bofip_chunk_tsv ON BOFIP;
CREATE TRIGGER trg_bofip_chunk_tsv
    BEFORE INSERT OR UPDATE OF chunk_text ON BOFIP
    FOR EACH ROW EXECUTE FUNCTION tsvector_update_chunk_tsv();

DO $$
DECLARE
    batch_size INT := 5000;
    updated INT;
BEGIN
    LOOP
        UPDATE BOFIP SET chunk_tsv = to_tsvector('french', coalesce(chunk_text, ''))
        WHERE chunk_id IN (
            SELECT chunk_id FROM BOFIP WHERE chunk_tsv IS NULL LIMIT batch_size
        );
        GET DIAGNOSTICS updated = ROW_COUNT;
        EXIT WHEN updated = 0;
        RAISE NOTICE 'BOFIP: backfilled % rows', updated;
    END LOOP;
END $$;

ANALYZE LEGI;
ANALYZE JADE;
ANALYZE BOFIP;
