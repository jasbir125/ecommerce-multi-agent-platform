-- =============================================================================
-- Ecommerce Multi-Agent Order Recovery Platform - bootstrap schema (PostgreSQL 16)
--
-- Runs once on first container start. Later phases will move this into
-- per-service Flyway migrations. VECTOR(1536) must match the embedding model.
-- =============================================================================
CREATE EXTENSION IF NOT EXISTS vector;

-- ------------------------------------------------------------------ workflow
-- One row per recovery run. state_json holds the versioned workflow state.
CREATE TABLE workflow_instance (
    workflow_id   VARCHAR(100) PRIMARY KEY,
    request_id    VARCHAR(100) NOT NULL,
    customer_id   VARCHAR(100),
    order_id      VARCHAR(100),
    status        VARCHAR(50)  NOT NULL,
    state_json    JSONB        NOT NULL,
    version       BIGINT       NOT NULL DEFAULT 0,          -- optimistic concurrency
    created_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX uq_workflow_request ON workflow_instance (request_id);  -- idempotent creation
CREATE INDEX idx_workflow_order  ON workflow_instance (order_id);
CREATE INDEX idx_workflow_status ON workflow_instance (status);

-- Append-only audit trail (agent decisions, tool calls, business actions...).
CREATE TABLE workflow_audit (
    id             BIGSERIAL PRIMARY KEY,
    workflow_id    VARCHAR(100) NOT NULL,
    agent_name     VARCHAR(100),
    event_type     VARCHAR(100) NOT NULL,
    payload        JSONB        NOT NULL,
    actor          VARCHAR(100),
    correlation_id VARCHAR(100),
    causation_id   VARCHAR(100),
    created_at     TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_audit_workflow ON workflow_audit (workflow_id, created_at);

-- Block UPDATE/DELETE so audit records are immutable.
CREATE FUNCTION forbid_audit_mutation() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'workflow_audit is append-only';
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_workflow_audit_immutable
    BEFORE UPDATE OR DELETE ON workflow_audit
    FOR EACH ROW EXECUTE FUNCTION forbid_audit_mutation();

CREATE TABLE approval_request (
    approval_id      VARCHAR(100) PRIMARY KEY,
    workflow_id      VARCHAR(100) NOT NULL,
    status           VARCHAR(50)  NOT NULL,                  -- PENDING/APPROVED/REJECTED/EXPIRED
    requested_action VARCHAR(100) NOT NULL,
    amount           NUMERIC(12,2),
    reason           TEXT,
    risk_level       VARCHAR(20),
    decided_by       VARCHAR(100),
    decided_at       TIMESTAMP,
    expires_at       TIMESTAMP,
    created_at       TIMESTAMP    DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_approval_workflow ON approval_request (workflow_id);
CREATE INDEX idx_approval_status   ON approval_request (status);

-- ------------------------------------------------------------- policy / RAG
-- A policy document is versioned; retrieval only sees ACTIVE documents that are in force
-- for the requested date. Filter columns live here so every chunk inherits them.
CREATE TABLE policy_document (
    id               BIGSERIAL PRIMARY KEY,
    policy_key       VARCHAR(200),
    version          VARCHAR(50),
    title            VARCHAR(500),
    content          TEXT,
    content_hash     VARCHAR(64),                            -- dedup / incremental updates
    policy_type      VARCHAR(50)  NOT NULL DEFAULT 'general', -- refund, shipping, replacement...
    tenant_id        VARCHAR(50)  NOT NULL DEFAULT 'default',
    customer_segment VARCHAR(50)  NOT NULL DEFAULT 'ALL',
    product_category VARCHAR(100) NOT NULL DEFAULT 'ALL',
    language         VARCHAR(10)  NOT NULL DEFAULT 'en',
    source_uri       VARCHAR(500),
    status           VARCHAR(20)  NOT NULL DEFAULT 'ACTIVE'
                     CHECK (status IN ('DRAFT','ACTIVE','ARCHIVED')),
    effective_from   DATE,
    effective_to     DATE,
    region           VARCHAR(10),
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (policy_key, version)
);
CREATE INDEX idx_policy_doc_filters ON policy_document (status, tenant_id, policy_type, region);

CREATE TABLE policy_chunk (
    id                 BIGSERIAL PRIMARY KEY,
    policy_document_id BIGINT REFERENCES policy_document(id),
    chunk_index        INT   NOT NULL,
    section            VARCHAR(300),                         -- heading the chunk sits under
    content            TEXT  NOT NULL,
    content_hash       VARCHAR(64) NOT NULL,                 -- lets an unchanged chunk reuse its embedding
    token_count        INT,
    metadata           JSONB NOT NULL,                       -- keywords, injectionSuspected, ...
    embedding_model    VARCHAR(100),                         -- vectors from different models are not comparable
    embedding          VECTOR(1536),
    -- Generated tsvector powers the keyword half of hybrid retrieval.
    content_tsv        TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
    created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (policy_document_id, chunk_index)
);

-- HNSW index: approximate nearest-neighbour search on cosine distance.
CREATE INDEX idx_policy_chunk_embedding
    ON policy_chunk USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX idx_policy_chunk_tsv      ON policy_chunk USING gin (content_tsv);
CREATE INDEX idx_policy_chunk_document ON policy_chunk (policy_document_id);
CREATE INDEX idx_policy_chunk_reuse    ON policy_chunk (content_hash, embedding_model);

-- Async ingestion jobs (one row per request, updated as the pipeline runs).
CREATE TABLE ingestion_job (
    job_id       VARCHAR(40) PRIMARY KEY,
    policy_key   VARCHAR(200) NOT NULL,
    version      VARCHAR(50)  NOT NULL,
    status       VARCHAR(20)  NOT NULL DEFAULT 'QUEUED'
                 CHECK (status IN ('QUEUED','RUNNING','DONE','UNCHANGED','FAILED')),
    stats        JSONB,
    error        TEXT,
    document_id  BIGINT,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    finished_at  TIMESTAMP
);

-- One row per retrieval, for quality and latency monitoring (no raw customer text: query is stored as a hash + a short preview).
CREATE TABLE rag_query_log (
    id               BIGSERIAL PRIMARY KEY,
    query_hash       VARCHAR(64) NOT NULL,
    query_preview    VARCHAR(120),
    latency_ms       INT,
    cache_hit        BOOLEAN,
    degraded         BOOLEAN,
    confidence       NUMERIC(4,3),
    insufficient     BOOLEAN,
    result_chunk_ids BIGINT[],
    created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_rag_query_log_time ON rag_query_log (created_at);

-- ---------------------------------------------------------- domain snapshots
-- Point-in-time copies of what the agents saw, kept for audit and evaluation.
CREATE TABLE order_snapshot (
    id            BIGSERIAL PRIMARY KEY,
    workflow_id   VARCHAR(100) NOT NULL,
    order_id      VARCHAR(100) NOT NULL,
    customer_id   VARCHAR(100) NOT NULL,
    status        VARCHAR(50),
    total_amount  NUMERIC(12,2),
    currency      CHAR(3) DEFAULT 'USD',
    snapshot_json JSONB NOT NULL,
    captured_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_order_snapshot_workflow ON order_snapshot (workflow_id);

CREATE TABLE shipment_snapshot (
    id            BIGSERIAL PRIMARY KEY,
    workflow_id   VARCHAR(100) NOT NULL,
    order_id      VARCHAR(100) NOT NULL,
    carrier       VARCHAR(100),
    tracking_no   VARCHAR(100),
    status        VARCHAR(50),
    delay_days    INT,
    snapshot_json JSONB NOT NULL,
    captured_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_shipment_snapshot_workflow ON shipment_snapshot (workflow_id);

-- ------------------------------------------------- authoritative side effects
CREATE TABLE payment_refund (
    refund_id       VARCHAR(100) PRIMARY KEY,
    workflow_id     VARCHAR(100) NOT NULL,
    order_id        VARCHAR(100) NOT NULL,
    approval_id     VARCHAR(100),
    amount          NUMERIC(12,2) NOT NULL CHECK (amount > 0),
    currency        CHAR(3) DEFAULT 'USD',
    status          VARCHAR(50)  NOT NULL,                   -- INITIATED/COMPLETED/FAILED
    idempotency_key VARCHAR(150) NOT NULL UNIQUE,            -- blocks duplicate refunds
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_refund_order ON payment_refund (order_id);

CREATE TABLE inventory_reservation (
    reservation_id  VARCHAR(100) PRIMARY KEY,
    workflow_id     VARCHAR(100) NOT NULL,
    product_id      VARCHAR(100) NOT NULL,
    quantity        INT NOT NULL CHECK (quantity > 0),
    status          VARCHAR(50) NOT NULL,                    -- RESERVED/RELEASED/FAILED
    idempotency_key VARCHAR(150) NOT NULL UNIQUE,
    created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    released_at     TIMESTAMP
);
CREATE INDEX idx_reservation_product ON inventory_reservation (product_id);

CREATE TABLE notification_record (
    id           BIGSERIAL PRIMARY KEY,
    workflow_id  VARCHAR(100) NOT NULL,
    channel      VARCHAR(20)  NOT NULL,                      -- EMAIL/SLACK/SMS
    recipient    VARCHAR(200) NOT NULL,                      -- masked in logs
    template     VARCHAR(100) NOT NULL,
    status       VARCHAR(50)  NOT NULL,                      -- QUEUED/SENT/FAILED
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    sent_at      TIMESTAMP
);
CREATE INDEX idx_notification_workflow ON notification_record (workflow_id);

-- ---------------------------------------------------------------- evaluation
CREATE TABLE evaluation_result (
    id           BIGSERIAL PRIMARY KEY,
    workflow_id  VARCHAR(100),
    dataset      VARCHAR(100),
    test_case    VARCHAR(100),
    metric       VARCHAR(100) NOT NULL,
    score        NUMERIC(5,4) NOT NULL,
    passed       BOOLEAN,
    detail       JSONB,
    created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_eval_metric ON evaluation_result (metric, created_at);
