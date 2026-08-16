"""Transactional RSS ingestion.  The SQLite store, not generated files, is history."""
import json
import uuid
from datetime import datetime
from pathlib import Path

from .db import PaperRepository, connect, now
from .importer import LegacyImporter


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


def ensure_database(root=".", database=None):
    """Create a CI-local database by importing committed compatibility exports once."""
    root = Path(root)
    database = str(database or root / "data" / "paper_feed.sqlite3")
    if not Path(database).exists():
        LegacyImporter(root, database).run(_backup_enabled=False)
    return database


def ingest_fetch_results(results, root=".", database=None, predicate=None):
    """Persist one fetch attempt and every successful observation atomically.

    A total outage intentionally commits only the audit rows.  Any insertion failure
    rolls back the entire run, so a partial paper set is never published as success.
    """
    database = ensure_database(root, database)
    conn = connect(database)
    repo = PaperRepository(conn)
    run_id = str(uuid.uuid4())
    successes = [r for r in results if r and r.get("success")]
    status = "succeeded" if len(successes) == len(results) else ("partial_failed" if successes else "failed")
    imported = new_observations = 0
    before_papers = conn.execute("SELECT count(*) FROM papers").fetchone()[0]
    try:
        with repo.transaction():
            conn.execute("INSERT INTO fetch_runs(run_id,started_at,status,dry_run) VALUES (?,?,?,0)", (run_id, now(), "running"))
            for result in results:
                source = (result or {}).get("url") or "unknown"
                ok = bool(result and result.get("success"))
                fetched = result.get("entries", []) if ok else []
                entries = [entry for entry in fetched if predicate is None or predicate(entry)]
                detail = {key: (result or {}).get(key) for key in ("status_code", "attempts", "error")}
                detail.update({"fetched_count": len(fetched), "matched_count": len(entries)})
                conn.execute("INSERT INTO source_fetches(run_id,source,status,item_count,detail_json) VALUES (?,?,?,?,?)",
                             (run_id, source, "succeeded" if ok else "failed", len(entries), json.dumps(detail)))
                if not ok:
                    continue
                for entry in entries:
                    record = dict(entry)
                    record["source"] = source
                    record["guid"] = record.get("guid") or record.get("id") or record.get("link")
                    record["pub_date"] = _iso(record.get("pub_date"))
                    exists = conn.execute("SELECT 1 FROM paper_observations WHERE source=? AND source_guid=?", (source, record["guid"])).fetchone()
                    paper_id = repo.resolve(record)
                    repo.ensure_inbox(paper_id)
                    stamp = now()
                    conn.execute("""INSERT INTO paper_observations(paper_id,source,source_guid,link,title,journal,published_at,summary,payload_json,first_seen_at,last_seen_at)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?)
                        ON CONFLICT(source,source_guid) DO UPDATE SET paper_id=excluded.paper_id,link=excluded.link,title=excluded.title,
                        journal=excluded.journal,published_at=excluded.published_at,summary=excluded.summary,payload_json=excluded.payload_json,last_seen_at=excluded.last_seen_at""",
                                 (paper_id, source, record["guid"], record.get("link"), record.get("title"), record.get("journal"),
                                  record.get("pub_date"), record.get("summary"), json.dumps(record, default=_iso), stamp, stamp))
                    imported += 1
                    new_observations += not bool(exists)
            new_papers = conn.execute("SELECT count(*) FROM papers").fetchone()[0] - before_papers
            summary = {"successful_sources": len(successes), "failed_sources": len(results) - len(successes), "observations": imported, "new_observations": new_observations, "new_papers": new_papers}
            conn.execute("UPDATE fetch_runs SET completed_at=?,status=?,summary_json=? WHERE run_id=?", (now(), status, json.dumps(summary), run_id))
    finally:
        conn.close()
    return {"run_id": run_id, "status": status, "successful_sources": [r["url"] for r in successes],
            "failed_sources": [(r or {}).get("url") for r in results if not r or not r.get("success")], "observations": imported,
            "new_observations": new_observations, "new_papers": new_papers}


def save_translations(database, records_by_id):
    """Persist GPT classifications by durable ID so exports never rely on a cache."""
    if not records_by_id:
        return 0
    conn = connect(database)
    try:
        repo = PaperRepository(conn)
        with repo.transaction():
            for paper_id, payload in records_by_id.items():
                conn.execute("""INSERT INTO paper_analyses(paper_id,analysis_kind,analysis_version,payload_json,updated_at)
                  VALUES (?,'translation','',?,?) ON CONFLICT(paper_id,analysis_kind,analysis_version) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                             (paper_id, json.dumps(payload), now()))
        return len(records_by_id)
    finally:
        conn.close()


def save_abstracts(database, records_by_id):
    """Persist generated abstracts by durable ID before compatibility projection."""
    if not records_by_id:
        return 0
    conn = connect(database)
    try:
        repo = PaperRepository(conn)
        with repo.transaction():
            for paper_id, payload in records_by_id.items():
                conn.execute("""INSERT INTO paper_analyses(paper_id,analysis_kind,analysis_version,payload_json,updated_at)
                  VALUES (?,'abstract','',?,?) ON CONFLICT(paper_id,analysis_kind,analysis_version) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                             (paper_id, json.dumps(payload), now()))
        return len(records_by_id)
    finally:
        conn.close()
