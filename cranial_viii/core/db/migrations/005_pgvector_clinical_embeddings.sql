-- CranialVIII Migration 005: Offline Vector Embeddings for Local Clinical Intelligence
-- Supports local embedding models (nomic-embed-text / bge-small-en) via pgvector

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS clinical_embeddings (
    id BIGSERIAL PRIMARY KEY,
    document_id BIGINT NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INT NOT NULL DEFAULT 0,
    chunk_text TEXT NOT NULL,
    embedding vector(768),               -- Standard 768-dim (nomic-embed-text) or configurable
    category VARCHAR(64) DEFAULT 'clinical_note', -- 'note', 'vng_report', 'audiogram', 'mri'
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_embeddings_doc_id ON clinical_embeddings(document_id);
-- HNSW index for high-speed offline similarity search
CREATE INDEX IF NOT EXISTS idx_embeddings_hnsw ON clinical_embeddings USING hnsw (embedding vector_cosine_ops);
