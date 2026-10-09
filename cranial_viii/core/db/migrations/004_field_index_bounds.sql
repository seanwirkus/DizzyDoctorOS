-- CranialVIII Migration 004: Strict Field Index Bounds and Clinical Category Partitioning
-- Ensures zero ingestion crashes when clinical narratives or OCR dumps are inserted.

-- Additional safety bound on raw values up to 512 bytes for complex addresses / multi-word clinical phrases
CREATE INDEX IF NOT EXISTS idx_doc_fields_key_val_512
ON doc_fields (field_key, substr(field_raw_value, 1, 128))
WHERE field_raw_value IS NOT NULL;

-- Dedicated indexes for clinical symptoms and physical exam tests (Weber, Rinne, Dix-Hallpike)
CREATE INDEX IF NOT EXISTS idx_doc_fields_symptoms
ON doc_fields (field_key, field_parsed_value)
WHERE field_category = 'symptom' AND length(field_parsed_value) <= 128;

CREATE INDEX IF NOT EXISTS idx_doc_fields_exam
ON doc_fields (field_key, field_parsed_value)
WHERE field_category = 'exam' AND length(field_parsed_value) <= 128;
