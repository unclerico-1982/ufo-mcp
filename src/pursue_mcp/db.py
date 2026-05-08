"""SQLite cache + migration runner."""

from __future__ import annotations

import json
import logging
import sqlite3
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from platformdirs import user_cache_path

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def default_db_path() -> Path:
    return user_cache_path("pursue-mcp", ensure_exists=True) / "index.sqlite"


@dataclass
class IncidentLocation:
    raw: str | None = None
    country: str | None = None
    region: str | None = None
    lat: float | None = None
    lon: float | None = None


@dataclass
class Attachment:
    kind: str = "other"        # open-ended; pdf | image | video | other | ...
    url: str = ""
    sha256: str | None = None
    ocr_done: bool = False


@dataclass
class Record:
    id: str
    source: str                # pursue | aaro | fbi_vault | nara
    title: str
    type: str | None = None
    originating_agency: str | None = None
    originating_file_id: str | None = None
    release_date: str | None = None
    incident_date: str | None = None
    incident_location: IncidentLocation = field(default_factory=IncidentLocation)
    classification: str | None = None
    attachments: list[Attachment] = field(default_factory=list)
    summary: str | None = None
    raw_text: str | None = None
    tags: list[str] = field(default_factory=list)
    fetched_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    source_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d


def connect(path: Path | None = None) -> sqlite3.Connection:
    p = path or default_db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    """Apply any pending SQL migrations from the migrations/ directory."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (filename TEXT PRIMARY KEY, applied_at TEXT)"
    )
    applied = {row["filename"] for row in conn.execute("SELECT filename FROM schema_migrations")}
    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    for f in files:
        if f.name in applied:
            continue
        log.info("applying migration %s", f.name)
        sql = f.read_text(encoding="utf-8")
        with conn:
            conn.executescript(sql)
            conn.execute(
                "INSERT INTO schema_migrations(filename, applied_at) VALUES (?, ?)",
                (f.name, datetime.now(UTC).isoformat()),
            )


def upsert_records(conn: sqlite3.Connection, records: Iterable[Record]) -> int:
    n = 0
    with conn:
        for r in records:
            conn.execute(
                """
                INSERT INTO records (
                    id, source, originating_agency, originating_file_id, title, type,
                    release_date, incident_date, incident_location_json, classification,
                    attachments_json, summary, raw_text, tags_json, fetched_at, source_url
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(id) DO UPDATE SET
                    source=excluded.source,
                    originating_agency=excluded.originating_agency,
                    originating_file_id=excluded.originating_file_id,
                    title=excluded.title,
                    type=excluded.type,
                    release_date=excluded.release_date,
                    incident_date=excluded.incident_date,
                    incident_location_json=excluded.incident_location_json,
                    classification=excluded.classification,
                    attachments_json=excluded.attachments_json,
                    summary=excluded.summary,
                    raw_text=excluded.raw_text,
                    tags_json=excluded.tags_json,
                    fetched_at=excluded.fetched_at,
                    source_url=excluded.source_url
                """,
                (
                    r.id,
                    r.source,
                    r.originating_agency,
                    r.originating_file_id,
                    r.title,
                    r.type,
                    r.release_date,
                    r.incident_date,
                    json.dumps(asdict(r.incident_location)),
                    r.classification,
                    json.dumps([asdict(a) for a in r.attachments]),
                    r.summary,
                    r.raw_text,
                    json.dumps(r.tags),
                    r.fetched_at,
                    r.source_url,
                ),
            )
            n += 1
    return n
