"""Migration runner + record upsert smoke tests."""

from __future__ import annotations

from pathlib import Path

from pursue_mcp import db


def test_migrate_creates_records_and_fts(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "idx.sqlite")
    db.migrate(conn)
    tables = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"records", "attachments", "record_attachments", "schema_migrations"} <= tables
    assert any("records_fts" in t for t in tables)

    db.migrate(conn)
    applied = list(conn.execute("SELECT filename FROM schema_migrations"))
    assert len(applied) == 1


def test_upsert_record_and_fts_search(tmp_path: Path) -> None:
    conn = db.connect(tmp_path / "idx.sqlite")
    db.migrate(conn)
    rec = db.Record(
        id="aaro:test:1",
        source="aaro",
        title="Unresolved infrared track over Pacific",
        type="report",
        summary="Cold thermal contact, parallax suggests high altitude.",
        raw_text="The contact persisted for 14 minutes before fading.",
        source_url="https://www.aaro.mil/test",
    )
    db.upsert_records(conn, [rec])

    rows = list(conn.execute("SELECT id, title FROM records"))
    assert len(rows) == 1
    fts_rows = list(
        conn.execute("SELECT rowid FROM records_fts WHERE records_fts MATCH 'parallax'")
    )
    assert len(fts_rows) == 1
