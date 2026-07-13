-- Canonical schema for the Phase 2 chunks table.
--
-- Applied idempotently by `ingestion.ingest` before every run (plain DDL, no
-- migration tool yet — Phase 4's SQLAlchemy models will map onto this table).
-- Every statement is guarded so re-running is a no-op on an already-built DB.

CREATE EXTENSION IF NOT EXISTS vector;

-- One row per (document, section): a self-contained, attributed retrieval unit.
CREATE TABLE IF NOT EXISTS chunks (
    id               BIGSERIAL PRIMARY KEY,
    doc_code         TEXT NOT NULL,        -- course/spec code, e.g. COMP1511 / COMPA1
    career           TEXT NOT NULL,        -- undergraduate | postgraduate | research
    content_type     TEXT NOT NULL,        -- courses | specialisations
    title            TEXT NOT NULL,
    section_type     TEXT NOT NULL,        -- overview | enrolment_conditions | offering | learning_outcomes | additional
    text             TEXT NOT NULL,        -- self-contained chunk text (code + title prefixed)
    credit_points    INT,                  -- units of credit (UOC), when known
    offering_terms   TEXT[],               -- e.g. {T1,T2,T3}
    rule_type        TEXT,                 -- enrolment_conditions only: prerequisite | corequisite | exclusion | equivalent | enrolment_requirement
    referenced_codes TEXT[],               -- course codes cited by a rule (queryable)
    source_url       TEXT NOT NULL,
    scraped_at       TIMESTAMPTZ NOT NULL,
    embedding        vector(384),          -- bge-small-en-v1.5
    -- Generated so the lexical half of hybrid retrieval never drifts from `text`.
    tsv              tsvector GENERATED ALWAYS AS (to_tsvector('english', text)) STORED
    -- No natural unique key: a document has several chunks of the same
    -- section_type (e.g. one enrolment_conditions row per rule). Idempotency is
    -- instead enforced by ingest: a per-document DELETE before re-insert.
);

-- Speeds the per-document DELETE that makes re-ingest idempotent.
CREATE INDEX IF NOT EXISTS chunks_doc_idx ON chunks (doc_code, career);

-- Lexical half of hybrid retrieval (ts_rank over this GIN index in Phase 3).
CREATE INDEX IF NOT EXISTS chunks_tsv_gin ON chunks USING GIN (tsv);

-- No ANN index at this scale (~1-2k rows): an exact vector scan is both faster
-- and exact. Revisit HNSW in Phase 3 only if probe-query latency warrants it.
