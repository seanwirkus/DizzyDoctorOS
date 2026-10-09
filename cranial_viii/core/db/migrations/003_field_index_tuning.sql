-- CranialVIII Migration 003: Field Index Tuning for Large Clinical Prose
-- Fixes PostgreSQL error: "index row size 2712 exceeds btree maximum 2704"
-- Free-text clinical notes hold kilobytes of prose which blow past the 2704-byte btree limit.
-- Drops unbounded btree indexes on values and replaces with length-bounded partial indexes.

DROP INDEX IF EXISTS idx_doc_fields_val;
DROP INDEX IF EXISTS idx_doc_fields_parsed;

-- Bounded partial index for short parsed values (dates, surnames, codes, symptoms <= 256 bytes)
CREATE INDEX IF NOT EXISTS idx_doc_fields_parsed_short
ON doc_fields (field_key, field_parsed_value)
WHERE length(field_parsed_value) <= 256;

-- Bounded partial index for short raw values
CREATE INDEX IF NOT EXISTS idx_doc_fields_raw_short
ON doc_fields (field_key, field_raw_value)
WHERE length(field_raw_value) <= 256;

-- Hash index for arbitrary length exact lookups without btree size limits
CREATE INDEX IF NOT EXISTS idx_doc_fields_parsed_hash
ON doc_fields USING HASH (field_parsed_value);
