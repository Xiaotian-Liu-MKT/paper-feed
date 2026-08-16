"""SQLite schema and repository primitives for Paper Feed."""
import contextlib
import os
import sqlite3
import uuid
from datetime import datetime, timezone

from .identity import canonical_url, fingerprint, identifiers, norm_text

SCHEMA_VERSION = 2

DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS papers (paper_id TEXT PRIMARY KEY, title TEXT NOT NULL, journal TEXT, published_at TEXT, canonical_url TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_identifiers (identifier_type TEXT NOT NULL, identifier_value TEXT NOT NULL, paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE, created_at TEXT NOT NULL, PRIMARY KEY(identifier_type, identifier_value));
CREATE INDEX IF NOT EXISTS idx_paper_identifiers_paper ON paper_identifiers(paper_id);
CREATE TABLE IF NOT EXISTS paper_observations (observation_id INTEGER PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE, source TEXT NOT NULL, source_guid TEXT, link TEXT, title TEXT, journal TEXT, published_at TEXT, summary TEXT, payload_json TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, UNIQUE(source, source_guid));
CREATE TABLE IF NOT EXISTS paper_review_state (paper_id TEXT PRIMARY KEY REFERENCES papers(paper_id) ON DELETE CASCADE, state TEXT NOT NULL CHECK(state IN ('inbox','favorite','archived','hidden')), state_changed_at TEXT NOT NULL, inboxed_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_review_events (event_id INTEGER PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE, event_type TEXT NOT NULL, event_key TEXT UNIQUE, payload_json TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS paper_analyses (analysis_id INTEGER PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE, analysis_kind TEXT NOT NULL, analysis_version TEXT NOT NULL DEFAULT '', payload_json TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(paper_id, analysis_kind, analysis_version));
CREATE TABLE IF NOT EXISTS paper_user_overrides (override_id INTEGER PRIMARY KEY, paper_id TEXT NOT NULL REFERENCES papers(paper_id) ON DELETE CASCADE, override_kind TEXT NOT NULL, payload_json TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(paper_id, override_kind));
CREATE TABLE IF NOT EXISTS fetch_runs (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, completed_at TEXT, status TEXT NOT NULL, dry_run INTEGER NOT NULL DEFAULT 0, summary_json TEXT);
CREATE TABLE IF NOT EXISTS source_fetches (source_fetch_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES fetch_runs(run_id) ON DELETE CASCADE, source TEXT NOT NULL, status TEXT NOT NULL, item_count INTEGER NOT NULL DEFAULT 0, detail_json TEXT, UNIQUE(run_id, source));
CREATE TABLE IF NOT EXISTS migration_unresolved (unresolved_id INTEGER PRIMARY KEY, source_kind TEXT NOT NULL, legacy_key TEXT NOT NULL, reason TEXT NOT NULL, payload_json TEXT, created_at TEXT NOT NULL, resolved_at TEXT, UNIQUE(source_kind, legacy_key, reason));
"""

COUNT_TABLES = {
    "papers": "papers", "identifiers": "paper_identifiers", "observations": "paper_observations",
    "review_states": "paper_review_state", "review_events": "paper_review_events",
    "analyses": "paper_analyses", "overrides": "paper_user_overrides", "unresolved": "migration_unresolved",
    "fetch_runs": "fetch_runs", "source_fetches": "source_fetches",
}


def now():
    return datetime.now(timezone.utc).isoformat()


def _migrate_review_state_v1(conn):
    columns = {row[1] for row in conn.execute("PRAGMA table_info(paper_review_state)")}
    if not columns or "state" in columns:
        return
    conn.execute("ALTER TABLE paper_review_state RENAME TO paper_review_state_v1")
    conn.execute("""CREATE TABLE paper_review_state (
        paper_id TEXT PRIMARY KEY REFERENCES papers(paper_id) ON DELETE CASCADE,
        state TEXT NOT NULL CHECK(state IN ('inbox','favorite','archived','hidden')),
        state_changed_at TEXT NOT NULL, inboxed_at TEXT NOT NULL)""")
    conn.execute("""INSERT INTO paper_review_state(paper_id, state, state_changed_at, inboxed_at)
        SELECT paper_id, CASE WHEN hidden THEN 'hidden' WHEN archived THEN 'archived'
                              WHEN favorite THEN 'favorite' ELSE 'inbox' END,
        updated_at, updated_at FROM paper_review_state_v1""")
    conn.execute("DROP TABLE paper_review_state_v1")


def connect(path="data/paper_feed.sqlite3"):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.executescript(DDL)
    _migrate_review_state_v1(conn)
    conn.execute("INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)", (SCHEMA_VERSION, now()))
    conn.commit()
    return conn


def database_counts(conn):
    return {name: conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for name, table in COUNT_TABLES.items()}


class PaperRepository:
    def __init__(self, conn):
        self.conn = conn

    @contextlib.contextmanager
    def transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield self
        except Exception:
            self.conn.rollback()
            raise
        else:
            self.conn.commit()

    def resolve(self, record, create=True):
        """Return a durable paper_id; title-only records never merge."""
        choices = identifiers(record)
        paper_fingerprint = fingerprint(record)
        if paper_fingerprint:
            choices.append(paper_fingerprint)
        matched_ids = {
            row[0]
            for kind, value in choices
            for row in self.conn.execute(
                "SELECT paper_id FROM paper_identifiers WHERE identifier_type=? AND identifier_value=?",
                (kind, value),
            )
        }
        if len(matched_ids) > 1:
            raise ValueError("conflicting existing paper identities")

        stamp = now()
        if matched_ids:
            paper_id = matched_ids.pop()
            self.conn.execute(
                "UPDATE papers SET updated_at=?, canonical_url=COALESCE(canonical_url, ?) WHERE paper_id=?",
                (stamp, canonical_url(record.get("link")), paper_id),
            )
        elif not create:
            return None
        else:
            paper_id = str(uuid.uuid4())
            self.conn.execute(
                "INSERT INTO papers VALUES (?, ?, ?, ?, ?, ?, ?)",
                (paper_id, record.get("title") or "(untitled)", record.get("journal"),
                 record.get("pub_date"), canonical_url(record.get("link")), stamp, stamp),
            )

        for kind, value in choices:
            self.conn.execute(
                "INSERT OR IGNORE INTO paper_identifiers VALUES (?, ?, ?, ?)",
                (kind, value, paper_id, stamp),
            )
        return paper_id

    def ensure_inbox(self, paper_id):
        stamp = now()
        self.conn.execute(
            "INSERT OR IGNORE INTO paper_review_state VALUES (?, 'inbox', ?, ?)",
            (paper_id, stamp, stamp),
        )

    def legacy_paper_id(self, legacy_key):
        row = self.conn.execute(
            "SELECT paper_id FROM paper_identifiers WHERE identifier_type='legacy_id' AND identifier_value=?",
            (str(legacy_key),),
        ).fetchone()
        return (row[0] if row else None) or self.resolve({"link": legacy_key}, create=False)

    def unique_title_paper_id(self, title):
        normalized = norm_text(title)
        if not normalized:
            return None, "empty title"
        rows = self.conn.execute("SELECT paper_id, title FROM papers").fetchall()
        candidates = [row[0] for row in rows if norm_text(row[1]) == normalized]
        if len(candidates) == 1:
            return candidates[0], None
        if not candidates:
            return None, "no paper with matching title"
        return None, "ambiguous normalized title"

    def add_legacy_alias(self, paper_id, legacy_key):
        if not legacy_key:
            return
        row = self.conn.execute(
            "SELECT paper_id FROM paper_identifiers WHERE identifier_type='legacy_id' AND identifier_value=?",
            (str(legacy_key),),
        ).fetchone()
        if row and row[0] != paper_id:
            raise ValueError("conflicting legacy id alias")
        self.conn.execute(
            "INSERT OR IGNORE INTO paper_identifiers VALUES ('legacy_id', ?, ?, ?)",
            (str(legacy_key), paper_id, now()),
        )
