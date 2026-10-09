-- CranialVIII Migration 001: Initial Core Schema
-- Sets up documents, doc_fields, crawl_manifest, conflict_queue, and form_templates

CREATE TABLE IF NOT EXISTS documents (
    id BIGSERIAL PRIMARY KEY,
    filepath TEXT UNIQUE NOT NULL,
    filename TEXT NOT NULL,
    extension VARCHAR(16) NOT NULL,
    file_size_bytes BIGINT NOT NULL,
    file_hash_sha256 VARCHAR(64) NOT NULL,
    is_duplicate BOOLEAN DEFAULT FALSE,
    duplicate_of_id BIGINT REFERENCES documents(id) ON DELETE SET NULL,
    
    -- Extracted High-Level Patient Metadata
    patient_id VARCHAR(64),
    patient_last_name VARCHAR(128),
    patient_first_name VARCHAR(128),
    patient_dob DATE,
    date_of_service DATE,
    raw_date_of_service TEXT,
    document_type VARCHAR(64),
    template_id VARCHAR(64),
    
    -- Scored Quality & Conflict Metrics
    hdr_last VARCHAR(128),              -- Persisted surname extracted from header/form for auditing
    confidence_tier VARCHAR(16) NOT NULL DEFAULT 'unprocessed', -- 'high', 'medium', 'conflict', 'low'
    confidence_score NUMERIC(5,2),
    levenshtein_distance INT,
    
    -- Ingestion Timestamps
    crawled_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    extracted_at TIMESTAMPTZ,
    last_modified_at TIMESTAMPTZ NOT NULL
);

-- Structured Clinical Fields Table (1:Many for clinical values)
CREATE TABLE IF NOT EXISTS doc_fields (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    field_key VARCHAR(128) NOT NULL,
    field_raw_value TEXT,
    field_parsed_value TEXT,
    field_category VARCHAR(64),          -- 'symptom', 'exam', 'demographic', 'medication'
    confidence NUMERIC(5,2) DEFAULT 1.0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Conflict Review Queue (For the 2.3% discrepancies, e.g., Smith vs Smyth)
CREATE TABLE IF NOT EXISTS conflict_queue (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    status VARCHAR(32) NOT NULL DEFAULT 'pending', -- 'pending', 'resolved_form', 'resolved_filename', 'manual_edit', 'dismissed'
    filename_extracted_name VARCHAR(128),
    form_extracted_name VARCHAR(128),
    levenshtein_dist INT,
    reviewed_by VARCHAR(64),
    resolution_notes TEXT,
    resolved_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Form Templates Registry (162 templates discovered across the clinic corpus)
CREATE TABLE IF NOT EXISTS form_templates (
    id VARCHAR(64) PRIMARY KEY,
    template_name VARCHAR(255) NOT NULL,
    doc_type VARCHAR(64) NOT NULL,
    signature_tokens TEXT[] NOT NULL,
    header_regex TEXT,
    date_regex TEXT,
    name_order VARCHAR(16) DEFAULT 'last_first', -- 'last_first' or 'first_last'
    active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Ingest Crawl Manifest Tracking
CREATE TABLE IF NOT EXISTS crawl_manifest (
    id BIGSERIAL PRIMARY KEY,
    source_root TEXT NOT NULL,
    total_files_scanned BIGINT NOT NULL,
    total_bytes_scanned BIGINT NOT NULL,
    duration_seconds NUMERIC(8,2) NOT NULL,
    duplicate_count BIGINT NOT NULL,
    duplicate_bytes BIGINT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    completed_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Base Indexes
CREATE INDEX IF NOT EXISTS idx_docs_filename ON documents(filename);
CREATE INDEX IF NOT EXISTS idx_docs_file_hash ON documents(file_hash_sha256);
CREATE INDEX IF NOT EXISTS idx_docs_confidence ON documents(confidence_tier);
CREATE INDEX IF NOT EXISTS idx_docs_dos ON documents(date_of_service);
CREATE INDEX IF NOT EXISTS idx_docs_patient_last ON documents(patient_last_name);
CREATE INDEX IF NOT EXISTS idx_doc_fields_doc_id ON doc_fields(document_id);
CREATE INDEX IF NOT EXISTS idx_doc_fields_key ON doc_fields(field_key);
