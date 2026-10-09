-- CranialVIII Migration 002: HIPAA Audit Trail and Clinical Text Search

-- HIPAA Audit Trail (Strict logging of who inspected or modified clinical records)
CREATE TABLE IF NOT EXISTS hipaa_audit_log (
    id BIGSERIAL PRIMARY KEY,
    action VARCHAR(32) NOT NULL,            -- 'VIEW', 'EXPORT', 'RENAME', 'RESOLVE_CONFLICT', 'ROI_SEND'
    document_id BIGINT REFERENCES documents(id) ON DELETE SET NULL,
    actor_id VARCHAR(64) NOT NULL,
    actor_ip VARCHAR(45),
    details JSONB,
    occurred_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_audit_doc_id ON hipaa_audit_log(document_id);
CREATE INDEX IF NOT EXISTS idx_audit_occurred ON hipaa_audit_log(occurred_at);
CREATE INDEX IF NOT EXISTS idx_audit_actor ON hipaa_audit_log(actor_id);

-- Full text search document body index
ALTER TABLE documents ADD COLUMN IF NOT EXISTS text_tsv TSVECTOR;
CREATE INDEX IF NOT EXISTS idx_docs_text_tsv ON documents USING GIN(text_tsv);
