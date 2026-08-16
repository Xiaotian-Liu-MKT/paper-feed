"""Request-scoped SQLite service used by the Paper Feed HTTP API."""
import json
import os
import threading
import uuid
from pathlib import Path

from .db import PaperRepository, connect, now
from .importer import LEGACY_FILES, LegacyImporter


_INITIALIZATION_LOCK = threading.RLock()


class PaperNotFound(ValueError):
    pass


class PaperReferenceError(ValueError):
    pass


def _labels(value):
    if isinstance(value, list):
        return value
    return [value] if value else []


def _primary(labels, fallback):
    if not labels:
        return fallback
    first = labels[0]
    return first.get("name") if isinstance(first, dict) else first


class PaperFeedService:
    """Never retains a connection: each public call opens and closes one."""
    def __init__(self, root=".", database=None):
        self.root = Path(root)
        self.database = str(database or self.root / "data" / "paper_feed.sqlite3")

    def _ensure_database(self):
        # The importer is deliberately called only for a missing formal database.
        if not os.path.exists(self.database):
            # Server handlers create service instances per request.  This lock and
            # inner check make first-open import/backup a once-only operation.
            with _INITIALIZATION_LOCK:
                if not os.path.exists(self.database):
                    has_legacy = any((self.root / name).exists() for name in LEGACY_FILES)
                    if has_legacy:
                        LegacyImporter(self.root, self.database).run()
                    else:
                        conn = connect(self.database)
                        conn.close()

    def _connection(self):
        self._ensure_database()
        return connect(self.database)

    @staticmethod
    def _aliases(conn, paper_id):
        rows = conn.execute("SELECT identifier_type, identifier_value FROM paper_identifiers WHERE paper_id=?", (paper_id,))
        aliases = {row[0]: row[1] for row in rows}
        return aliases

    def _record(self, conn, paper_id):
        row = conn.execute("""SELECT p.*, s.state, s.state_changed_at, o.link, o.title AS observed_title,
            o.journal AS observed_journal, o.published_at AS observed_published_at, o.summary, o.payload_json
            FROM papers p JOIN paper_review_state s ON s.paper_id=p.paper_id
            LEFT JOIN paper_observations o ON o.observation_id=(SELECT MAX(observation_id) FROM paper_observations WHERE paper_id=p.paper_id)
            WHERE p.paper_id=?""", (paper_id,)).fetchone()
        if not row:
            return None
        payload = json.loads(row["payload_json"] or "{}")
        # Preserve current feed shape, while durable identifiers always win.
        item = payload if isinstance(payload, dict) else {}
        item.update({key: value for key, value in {
            "paper_id": paper_id, "id": item.get("id") or self._aliases(conn, paper_id).get("legacy_id"),
            "link": row["link"] or item.get("link") or row["canonical_url"], "title": row["observed_title"] or row["title"],
            "journal": row["observed_journal"] or row["journal"], "pub_date": row["observed_published_at"] or row["published_at"],
            "summary": row["summary"] or item.get("summary"), "state": row["state"],
        }.items() if value is not None})
        aliases = self._aliases(conn, paper_id)
        item["legacy_id"] = aliases.get("legacy_id") or item.get("id")
        item["legacy_link"] = item.get("link")
        def payload(table, column, kind):
            data = conn.execute(f"SELECT payload_json FROM {table} WHERE paper_id=? AND {column}=?", (paper_id, kind)).fetchone()
            try:
                parsed = json.loads(data[0]) if data else {}
            except (TypeError, json.JSONDecodeError):
                parsed = {}
            return parsed if isinstance(parsed, dict) else {}

        # Keep the exact projection semantics of exporter.feed_items: AI
        # classification first, its abstract second, then non-empty human edits.
        # Copies ensure an API read never mutates stored JSON payloads.
        translation = dict(payload("paper_analyses", "analysis_kind", "translation"))
        abstract = dict(payload("paper_analyses", "analysis_kind", "abstract"))
        correction = dict(payload("paper_user_overrides", "override_kind", "user_correction"))
        methods = _labels(translation.get("methods", translation.get("method", [])))
        topics = _labels(translation.get("topics", translation.get("topic", [])))
        effective_correction = False
        for key in ("methods", "topics", "theories", "context", "subjects", "novelty_score"):
            if key in correction and correction[key] not in (None, [], ""):
                effective_correction = True
                if key == "methods":
                    methods = _labels(correction[key])
                elif key == "topics":
                    topics = _labels(correction[key])
                else:
                    translation[key] = correction[key]
        item.update({
            "title_zh": translation.get("zh", ""), "methods": methods, "topics": topics,
            "method": _primary(methods, "Qualitative"), "topic": _primary(topics, "Other Marketing"),
            "theories": translation.get("theories", []), "context": translation.get("context", []),
            "subjects": translation.get("subjects", []), "novelty_score": translation.get("novelty_score"),
            "classification_source": "user" if effective_correction else "gpt",
            "classification_version": translation.get("classification_version", ""),
            "user_corrected": effective_correction,
            "abstract": abstract.get("abstract", ""), "raw_abstract": abstract.get("raw_abstract", ""),
            "abstract_source": abstract.get("source", ""),
        })
        return item

    def list_papers(self, view="inbox"):
        if view not in {"inbox", "favorite", "archived", "hidden", "all"}:
            raise ValueError("view must be inbox, favorite, archived, hidden, or all")
        conn = self._connection()
        try:
            where = "" if view == "all" else "WHERE s.state=?"
            args = () if view == "all" else (view,)
            ids = conn.execute(f"""SELECT p.paper_id FROM papers p JOIN paper_review_state s ON s.paper_id=p.paper_id
                {where} ORDER BY COALESCE(p.published_at, '') DESC, p.title COLLATE NOCASE, p.paper_id""", args).fetchall()
            return [self._record(conn, row[0]) for row in ids]
        finally: conn.close()

    def get_paper(self, paper_id):
        conn = self._connection()
        try: return self._record(conn, paper_id)
        finally: conn.close()

    def resolve_reference(self, data, required=True):
        paper_id = data.get("paper_id")
        if paper_id:
            return paper_id
        legacy = data.get("id") or data.get("link")
        if not legacy:
            if required: raise PaperReferenceError("paper_id is required")
            return None
        conn = self._connection()
        try:
            repo = PaperRepository(conn)
            found = repo.legacy_paper_id(legacy)
            if not found: raise PaperReferenceError("legacy id/link does not resolve to a paper_id")
            return found
        finally: conn.close()

    def review(self, paper_id, action):
        targets = {"like": "favorite", "archive": "archived", "hide": "hidden", "restore": "favorite",
                   "unlike": "inbox", "unarchive": "inbox", "unhide": "inbox"}
        if action not in targets: raise ValueError("unknown review action")
        conn = self._connection()
        try:
            repo = PaperRepository(conn)
            with repo.transaction():
                old = conn.execute("SELECT state FROM paper_review_state WHERE paper_id=?", (paper_id,)).fetchone()
                if not old: raise PaperNotFound("paper_id not found")
                target = targets[action]
                if old[0] != target:
                    conn.execute("UPDATE paper_review_state SET state=?, state_changed_at=? WHERE paper_id=?", (target, now(), paper_id))
                    # A retry does not enter this branch; every real transition is
                    # nevertheless an independent audit event, including cycles.
                    key = f"review:{uuid.uuid4()}"
                    conn.execute("INSERT OR IGNORE INTO paper_review_events(paper_id,event_type,event_key,payload_json,created_at) VALUES (?,?,?,?,?)",
                                 (paper_id, action, key, json.dumps({"from": old[0], "to": target}), now()))
                result = self._record(conn, paper_id)
            return result
        finally: conn.close()

    def interactions(self):
        return {plural: [item["paper_id"] for item in self.list_papers(state)]
                for plural, state in (("favorites", "favorite"), ("archived", "archived"), ("hidden", "hidden"))}

    def save_abstract(self, paper_id, abstract):
        return self._save_payload(paper_id, "paper_analyses", "analysis_kind", "abstract",
                                  {"abstract": abstract, "raw_abstract": abstract, "source": "user_provided", "updated_at": now()})

    def save_classification(self, paper_id, payload):
        return self._save_payload(paper_id, "paper_user_overrides", "override_kind", "user_correction", payload)

    def _save_payload(self, paper_id, table, kind_col, kind, payload):
        conn = self._connection()
        try:
            repo = PaperRepository(conn)
            with repo.transaction():
                if not conn.execute("SELECT 1 FROM papers WHERE paper_id=?", (paper_id,)).fetchone(): raise PaperNotFound("paper_id not found")
                if table == "paper_analyses":
                    conn.execute("""INSERT INTO paper_analyses(paper_id,analysis_kind,analysis_version,payload_json,updated_at) VALUES (?,?,'',?,?)
                     ON CONFLICT(paper_id,analysis_kind,analysis_version) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at""", (paper_id, kind, json.dumps(payload), now()))
                else:
                    conn.execute("""INSERT INTO paper_user_overrides(paper_id,override_kind,payload_json,updated_at) VALUES (?,?,?,?)
                     ON CONFLICT(paper_id,override_kind) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at""", (paper_id, kind, json.dumps(payload), now()))
        finally: conn.close()

    def favorite_legacy_ids(self):
        conn = self._connection()
        try:
            rows = conn.execute("""SELECT s.paper_id,
                COALESCE((SELECT o.source_guid FROM paper_observations o WHERE o.paper_id=s.paper_id
                          AND o.source_guid IS NOT NULL ORDER BY o.last_seen_at DESC, o.observation_id DESC LIMIT 1),
                         (SELECT i.identifier_value FROM paper_identifiers i WHERE i.paper_id=s.paper_id
                          AND i.identifier_type='legacy_id' ORDER BY i.identifier_value LIMIT 1)) AS legacy_id
             FROM paper_review_state s WHERE s.state='favorite' ORDER BY s.state_changed_at, s.paper_id""").fetchall()
            return [(r[0], r[1]) for r in rows]
        finally: conn.close()
