-- v1: normalized record table + FTS5 mirror.
--
-- Normalized record schema mirrors the dict shape documented in CLAUDE.md.
-- attachments, incident_location, and tags are stored as JSON for now;
-- they can be promoted to relational tables once the FBI Vault and PURSUE
-- integrations make access patterns concrete.

CREATE TABLE IF NOT EXISTS records (
    id TEXT PRIMARY KEY,                 -- namespaced, e.g. aaro:case-2023-1234
    source TEXT NOT NULL,                -- pursue | aaro | fbi_vault | nara
    originating_agency TEXT,
    originating_file_id TEXT,
    title TEXT NOT NULL,
    type TEXT,                           -- record-level type; open-ended
    release_date TEXT,                   -- ISO-ish; partial dates preserved as string
    incident_date TEXT,
    incident_location_json TEXT,         -- JSON object: {raw, country, region, lat, lon}
    classification TEXT,
    attachments_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT,
    raw_text TEXT,
    tags_json TEXT NOT NULL DEFAULT '[]',
    fetched_at TEXT NOT NULL,
    source_url TEXT
);

CREATE INDEX IF NOT EXISTS idx_records_source ON records(source);
CREATE INDEX IF NOT EXISTS idx_records_release_date ON records(release_date);
CREATE INDEX IF NOT EXISTS idx_records_incident_date ON records(incident_date);
CREATE INDEX IF NOT EXISTS idx_records_originating_agency ON records(originating_agency);

-- Content-hash dedupe table: one row per unique attachment payload, joined to
-- records via a many-to-many table. Same Apollo 17 image across NASA + DOW
-- collapses to one attachments row with two record links.
CREATE TABLE IF NOT EXISTS attachments (
    sha256 TEXT PRIMARY KEY,
    kind TEXT NOT NULL,                  -- pdf | image | video | other
    url TEXT NOT NULL,
    bytes INTEGER,
    ocr_done INTEGER NOT NULL DEFAULT 0,
    ocr_text TEXT,
    fetched_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS record_attachments (
    record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    sha256 TEXT NOT NULL REFERENCES attachments(sha256) ON DELETE CASCADE,
    PRIMARY KEY (record_id, sha256)
);

-- FTS5 over title + summary + raw_text. External-content table; rebuild via
-- triggers so writes only happen against records.
CREATE VIRTUAL TABLE IF NOT EXISTS records_fts USING fts5(
    title, summary, raw_text,
    content='records', content_rowid='rowid',
    tokenize='porter'
);

CREATE TRIGGER IF NOT EXISTS records_ai AFTER INSERT ON records BEGIN
    INSERT INTO records_fts(rowid, title, summary, raw_text)
    VALUES (new.rowid, new.title, COALESCE(new.summary, ''), COALESCE(new.raw_text, ''));
END;

CREATE TRIGGER IF NOT EXISTS records_ad AFTER DELETE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, title, summary, raw_text)
    VALUES ('delete', old.rowid, old.title, COALESCE(old.summary, ''), COALESCE(old.raw_text, ''));
END;

CREATE TRIGGER IF NOT EXISTS records_au AFTER UPDATE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, title, summary, raw_text)
    VALUES ('delete', old.rowid, old.title, COALESCE(old.summary, ''), COALESCE(old.raw_text, ''));
    INSERT INTO records_fts(rowid, title, summary, raw_text)
    VALUES (new.rowid, new.title, COALESCE(new.summary, ''), COALESCE(new.raw_text, ''));
END;
