"""Transactional RSS ingestion.  The SQLite store, not generated files, is history."""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path

from .db import PaperRepository, connect, now
from .identity import normalize_doi, normalize_published_at
from .importer import LegacyImporter

# Raw abstracts fetched for free from metadata APIs (no AI involved).
FETCHED_ABSTRACT_SOURCES = frozenset({"crossref", "openalex", "semantic_scholar"})


def _iso(value):
    return value.isoformat() if isinstance(value, datetime) else (str(value) if value else None)


EMPTY_DATABASE_NOTICE = ("Created an empty Paper Feed database. "
                         "如需导入已有导出，运行 python -m paper_feed import-legacy")


def _env_truthy(name):
    return os.environ.get(name, "").strip().lower() not in {"", "0", "false", "no", "off"}


def should_bootstrap_from_exports():
    """Import committed exports only in CI or when explicitly requested.

    GitHub Actions has no persistent database and must rebuild history from
    the tracked exports; a fresh local clone must not silently inherit the
    repository author's papers.
    """
    return any(_env_truthy(name) for name in ("CI", "GITHUB_ACTIONS", "PAPER_FEED_BOOTSTRAP_FROM_EXPORTS"))


def ensure_database(root=".", database=None, bootstrap=None):
    """Return the database path, creating it when missing.

    A missing database is bootstrapped from committed compatibility exports
    only when *bootstrap* is True or, when None, ``should_bootstrap_from_exports()``
    (CI / GITHUB_ACTIONS / PAPER_FEED_BOOTSTRAP_FROM_EXPORTS=1).  Otherwise an
    empty database is created.
    """
    root = Path(root)
    database = str(database or root / "data" / "paper_feed.sqlite3")
    if not Path(database).exists():
        if bootstrap is None:
            bootstrap = should_bootstrap_from_exports()
        if bootstrap:
            LegacyImporter(root, database).run(_backup_enabled=False)
        else:
            connect(database).close()
            print(EMPTY_DATABASE_NOTICE)
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
                    # ISO 8601 when parseable so SQLite string ordering is chronological.
                    record["pub_date"] = normalize_published_at(record.get("pub_date"))
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


def _load_payload(text):
    try:
        value = json.loads(text or "{}")
        return value if isinstance(value, dict) else {}
    except (TypeError, ValueError):
        return {}


def _same_text(left, right):
    return " ".join(str(left or "").split()) == " ".join(str(right or "").split())


def _abstract_write_allowed(existing, payload):
    """Decide whether a job-generated abstract payload may replace *existing*.

    * A ``user_provided`` abstract is never replaced by a different raw text.
      A summary built from exactly the user's text is allowed (the raw text is
      kept and tagged ``raw_source='user_provided'``), so an edit saved while
      a job was running is never clobbered by a summary of older text.
    * A freshly fetched raw abstract (Crossref/OpenAlex/Semantic Scholar) only
      fills a gap: it never replaces a row that already has raw text.
    """
    if not existing:
        return True
    existing_raw = existing.get("raw_abstract") or (existing.get("abstract") if existing.get("source") == "user_provided" else "")
    if existing.get("source") == "user_provided" or existing.get("raw_source") == "user_provided":
        # An empty user edit (cleared abstract) has nothing left to protect.
        return not str(existing_raw or "").strip() or _same_text(existing_raw, payload.get("raw_abstract"))
    if payload.get("source") in FETCHED_ABSTRACT_SOURCES and existing_raw:
        return False
    return True


def save_abstracts(database, records_by_id):
    """Persist generated/fetched abstracts by durable ID; returns the number saved.

    Rows whose raw abstract came from the user are protected (see
    ``_abstract_write_allowed``); skipped rows are not counted.
    """
    if not records_by_id:
        return 0
    conn = connect(database)
    saved = 0
    try:
        repo = PaperRepository(conn)
        with repo.transaction():
            for paper_id, payload in records_by_id.items():
                row = conn.execute("SELECT payload_json FROM paper_analyses WHERE paper_id=? AND analysis_kind='abstract' AND analysis_version=''",
                                   (paper_id,)).fetchone()
                existing = _load_payload(row[0]) if row else {}
                if not _abstract_write_allowed(existing, payload):
                    continue
                payload = dict(payload)
                user_raw = existing.get("raw_abstract") or existing.get("abstract")
                if (existing.get("source") == "user_provided" or existing.get("raw_source") == "user_provided") and str(user_raw or "").strip():
                    payload["raw_source"] = "user_provided"
                conn.execute("""INSERT INTO paper_analyses(paper_id,analysis_kind,analysis_version,payload_json,updated_at)
                  VALUES (?,'abstract','',?,?) ON CONFLICT(paper_id,analysis_kind,analysis_version) DO UPDATE SET payload_json=excluded.payload_json,updated_at=excluded.updated_at""",
                             (paper_id, json.dumps(payload), now()))
                saved += 1
        return saved
    finally:
        conn.close()


def paper_dois(database, paper_ids=None):
    """{paper_id: normalized DOI} from stored identifiers (strongest identity first)."""
    conn = connect(database)
    try:
        rows = conn.execute("SELECT paper_id, identifier_value FROM paper_identifiers WHERE identifier_type='doi' ORDER BY created_at").fetchall()
    finally:
        conn.close()
    wanted = set(paper_ids) if paper_ids is not None else None
    result = {}
    for paper_id, value in rows:
        if wanted is not None and paper_id not in wanted:
            continue
        doi = normalize_doi(value)
        if doi:
            result.setdefault(paper_id, doi)
    return result


def paper_ids_in_view(database, view="favorite"):
    """paper_ids whose review state is *view* (``all``/None: every paper)."""
    conn = connect(database)
    try:
        if not view or view == "all":
            rows = conn.execute("SELECT paper_id FROM papers").fetchall()
        else:
            rows = conn.execute("SELECT paper_id FROM paper_review_state WHERE state=?", (view,)).fetchall()
        return [row[0] for row in rows]
    finally:
        conn.close()
