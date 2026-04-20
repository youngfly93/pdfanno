-- pdfanno sidecar schema v1。
-- 修改 schema 必须同时 bump schema_version 并写迁移路径 —— plan.md §9。

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '1');

CREATE TABLE IF NOT EXISTS doc_bindings (
    doc_id TEXT PRIMARY KEY,
    last_known_path TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entries (
    id TEXT PRIMARY KEY,
    annotation_id TEXT NOT NULL,
    doc_id TEXT NOT NULL,
    page INTEGER NOT NULL,
    kind TEXT NOT NULL,
    quads TEXT NOT NULL,
    color TEXT NOT NULL,
    contents TEXT NOT NULL DEFAULT '',
    matched_text TEXT NOT NULL DEFAULT '',
    rule_hash TEXT NOT NULL DEFAULT '',
    query TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    pdf_xref INTEGER,
    state TEXT NOT NULL,
    created_at TEXT NOT NULL,
    modified_at TEXT NOT NULL,
    UNIQUE (doc_id, annotation_id)
);

CREATE INDEX IF NOT EXISTS idx_entries_doc ON entries(doc_id);
CREATE INDEX IF NOT EXISTS idx_entries_state ON entries(doc_id, state);
