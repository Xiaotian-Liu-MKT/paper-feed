"""One-way, idempotent importer for the pre-SQLite Paper Feed files."""
import argparse
import json
import os
import shutil
import uuid
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from xml.etree import ElementTree as ET

from .db import PaperRepository, connect, database_counts, now

LEGACY_FILES = (
    "filtered_feed.xml", "web/feed.json", "web/interactions.json",
    "web/translations.json", "web/abstracts.json", "web/user_corrections.json",
)


def read_json(path, fallback):
    if not path.exists():
        return fallback
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


class LegacyImporter:
    def __init__(self, root=".", database=None):
        self.root = Path(root)
        self.database = database or str(self.root / "data" / "paper_feed.sqlite3")

    def records(self):
        records = []
        xml_path = self.root / "filtered_feed.xml"
        if xml_path.exists():
            for _, item in ET.iterparse(xml_path, events=("end",)):
                if item.tag.rsplit("}", 1)[-1] != "item":
                    continue
                values = {child.tag.rsplit("}", 1)[-1]: child.text or "" for child in item}
                records.append({
                    "source": "legacy_xml", "guid": values.get("guid"), "id": values.get("guid"),
                    "title": values.get("title"), "link": values.get("link"),
                    "journal": values.get("author"), "pub_date": values.get("pubDate"),
                    "summary": values.get("description"),
                })
                item.clear()
        feed = read_json(self.root / "web/feed.json", {}).get("items", [])
        for item in feed:
            if isinstance(item, dict):
                records.append({"source": "legacy_feed", "guid": item.get("id"), **item})
        return records

    def backup_legacy_files(self):
        source_files = [self.root / name for name in LEGACY_FILES if (self.root / name).exists()]
        if not source_files:
            return None
        backup_root = self.root / "data"
        if backup_root.exists() and any(backup_root.glob("paper_feed-legacy-backup-*")):
            return None
        directory = backup_root / f"paper_feed-legacy-backup-{datetime.now():%Y%m%dT%H%M%S%f}"
        for source in source_files:
            target = directory / source.relative_to(self.root)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        return str(directory)

    def run(self, dry_run=False, fail_after=None, _backup_enabled=True):
        if dry_run:
            return self._run_shadow(fail_after)
        return self._run_import(fail_after, _backup_enabled)

    def _run_shadow(self, fail_after):
        shadow = str(self.root / "data" / f".paper_feed_shadow_{uuid.uuid4().hex}.sqlite3")
        try:
            outcome = LegacyImporter(self.root, shadow)._run_import(fail_after, backup_enabled=False)
            outcome["dry_run"] = True
            outcome["backup"] = None
            return outcome
        finally:
            for suffix in ("", "-wal", "-shm"):
                try:
                    os.remove(shadow + suffix)
                except FileNotFoundError:
                    pass

    def _run_import(self, fail_after, backup_enabled):
        records = self.records()
        summary = Counter(records=len(records))
        unresolved = []
        backup = self.backup_legacy_files() if backup_enabled else None
        conn = connect(self.database)
        repo = PaperRepository(conn)
        run_id = str(uuid.uuid4())
        try:
            with repo.transaction():
                conn.execute(
                    "INSERT INTO fetch_runs(run_id, started_at, status, dry_run) VALUES (?, ?, 'running', 0)",
                    (run_id, now()),
                )
                self._import_records(conn, repo, records, summary, unresolved, fail_after)
                self._record_source_fetches(conn, run_id, records)
                self._import_metadata(conn, repo, unresolved)
                self._store_unresolved(conn, unresolved)
                counts = database_counts(conn)
                summary_json = json.dumps({"import": dict(summary), "database": counts})
                conn.execute(
                    "UPDATE fetch_runs SET completed_at=?, status='completed', summary_json=? WHERE run_id=?",
                    (now(), summary_json, run_id),
                )
            counts = database_counts(conn)
        except Exception:
            conn.close()
            raise
        conn.close()
        return {
            "run_id": run_id,
            "backup": backup,
            "dry_run": False,
            "import": dict(summary),
            "database": counts,
            "unresolved_in_run": len(unresolved),
        }

    def _import_records(self, conn, repo, records, summary, unresolved, fail_after):
        for index, record in enumerate(records, 1):
            try:
                paper_id = repo.resolve(record)
                repo.add_legacy_alias(paper_id, record.get("id") or record.get("guid"))
                repo.ensure_inbox(paper_id)
            except ValueError as exc:
                unresolved.append(("record", str(record.get("id") or record.get("link") or index), str(exc), record))
                continue
            stamp = now()
            conn.execute(
                """INSERT INTO paper_observations(
                    paper_id, source, source_guid, link, title, journal, published_at, summary,
                    payload_json, first_seen_at, last_seen_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source,source_guid) DO UPDATE SET
                    paper_id=excluded.paper_id, last_seen_at=excluded.last_seen_at,
                    payload_json=excluded.payload_json""",
                (paper_id, record.get("source") or "legacy", record.get("guid") or record.get("id") or record.get("link"),
                 record.get("link"), record.get("title"), record.get("journal"), record.get("pub_date"),
                 record.get("summary"), json.dumps(record), stamp, stamp),
            )
            summary["observations"] += 1
            if fail_after and index >= fail_after:
                raise RuntimeError("injected import failure")

    @staticmethod
    def _record_source_fetches(conn, run_id, records):
        for source, count in Counter(record.get("source") or "legacy" for record in records).items():
            conn.execute(
                "INSERT INTO source_fetches(run_id,source,status,item_count) VALUES (?,?,'imported',?)",
                (run_id, source, count),
            )

    def _import_metadata(self, conn, repo, unresolved):
        self._import_interactions(conn, repo, unresolved)
        self._import_cache(conn, repo, unresolved, "translations.json", "translation", "paper_analyses", title_keys=True)
        self._import_cache(conn, repo, unresolved, "abstracts.json", "abstract", "paper_analyses")
        self._import_cache(conn, repo, unresolved, "user_corrections.json", "user_correction", "paper_user_overrides")

    def _import_interactions(self, conn, repo, unresolved):
        interactions = read_json(self.root / "web/interactions.json", {})
        if not isinstance(interactions, dict):
            return
        states_for_key = defaultdict(set)
        for legacy_state in ("favorites", "archived", "hidden"):
            for key in interactions.get(legacy_state, []):
                states_for_key[str(key)].add(legacy_state)
        target_state = {"favorites": "favorite", "archived": "archived", "hidden": "hidden"}
        for key, states in states_for_key.items():
            if len(states) != 1:
                unresolved.append(("interaction", key, "conflicting legacy interaction states", {"states": sorted(states)}))
                continue
            legacy_state = next(iter(states))
            paper_id = repo.legacy_paper_id(key)
            if not paper_id:
                unresolved.append(("interaction", key, "no matching legacy paper", {"state": legacy_state}))
                continue
            stamp = now()
            conn.execute(
                "UPDATE paper_review_state SET state=?, state_changed_at=? WHERE paper_id=?",
                (target_state[legacy_state], stamp, paper_id),
            )
            conn.execute(
                """INSERT OR IGNORE INTO paper_review_events(
                    paper_id,event_type,event_key,payload_json,created_at)
                VALUES (?,?,?,?,?)""",
                (paper_id, target_state[legacy_state], f"legacy:{legacy_state}:{key}",
                 json.dumps({"legacy_key": key}), stamp),
            )

    def _import_cache(self, conn, repo, unresolved, filename, kind, table, title_keys=False):
        data = read_json(self.root / "web" / filename, {})
        if not isinstance(data, dict):
            return
        for key, payload in data.items():
            if title_keys:
                paper_id, reason = repo.unique_title_paper_id(key)
            else:
                paper_id = repo.legacy_paper_id(key)
                reason = "no matching legacy paper" if not paper_id else None
            if not paper_id:
                unresolved.append((kind, str(key), reason, payload))
                continue
            if table == "paper_analyses":
                conn.execute(
                    """INSERT INTO paper_analyses(paper_id,analysis_kind,analysis_version,payload_json,updated_at)
                    VALUES (?,?,'',?,?)
                    ON CONFLICT(paper_id,analysis_kind,analysis_version) DO UPDATE SET
                        payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                    (paper_id, kind, json.dumps(payload), now()),
                )
            else:
                conn.execute(
                    """INSERT INTO paper_user_overrides(paper_id,override_kind,payload_json,updated_at)
                    VALUES (?,?,?,?)
                    ON CONFLICT(paper_id,override_kind) DO UPDATE SET
                        payload_json=excluded.payload_json, updated_at=excluded.updated_at""",
                    (paper_id, kind, json.dumps(payload), now()),
                )

    @staticmethod
    def _store_unresolved(conn, unresolved):
        for kind, key, reason, payload in unresolved:
            conn.execute(
                """INSERT OR IGNORE INTO migration_unresolved(
                    source_kind,legacy_key,reason,payload_json,created_at)
                VALUES (?,?,?,?,?)""",
                (kind, key, reason, json.dumps(payload), now()),
            )


def import_legacy(root=".", database=None, dry_run=False):
    return LegacyImporter(root, database).run(dry_run=dry_run)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--database")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    print(json.dumps(LegacyImporter(args.root, args.database).run(args.dry_run), indent=2))


if __name__ == "__main__":
    main()
