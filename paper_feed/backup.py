"""Consistent online backups (and restores) of the Paper Feed SQLite database."""
import os
import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
SIDECAR_SUFFIXES = ("-wal", "-shm")


def default_database():
    return os.environ.get("PAPER_FEED_DB") or str(PROJECT_DIR / "data" / "paper_feed.sqlite3")


def _copy_with_backup_api(source_path, target_path):
    """Copy through the sqlite3 backup API (includes committed WAL content)."""
    source = sqlite3.connect(str(source_path))
    try:
        destination = sqlite3.connect(str(target_path))
        try:
            source.backup(destination)
        finally:
            destination.close()
    finally:
        source.close()


def inspect_database(path):
    """Return {"integrity", "papers", "states"} for a Paper Feed database file.

    Raises sqlite3.DatabaseError when *path* is not a SQLite database.
    """
    conn = sqlite3.connect(str(path))
    try:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        papers = conn.execute("SELECT count(*) FROM papers").fetchone()[0] if "papers" in tables else None
        states = dict(conn.execute("SELECT state, count(*) FROM paper_review_state GROUP BY state").fetchall()) \
            if "paper_review_state" in tables else {}
    finally:
        conn.close()
    return {"integrity": integrity, "papers": papers, "states": states}


def backup_database(database=None, out_dir=None, label="backup"):
    """Copy *database* with the sqlite3 backup API (safe while the server runs).

    The default name `paper_feed.sqlite3-backup-<timestamp>.sqlite3` in `data/`
    matches the repository's `data/paper_feed.sqlite3-*` ignore rule.
    """
    source_path = Path(database or default_database())
    if not source_path.exists():
        raise FileNotFoundError(f"database not found: {source_path}")
    target_dir = Path(out_dir) if out_dir else source_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"paper_feed.sqlite3-{label}-{datetime.now():%Y%m%dT%H%M%S%f}.sqlite3"
    _copy_with_backup_api(source_path, target)
    info = inspect_database(target)
    return {"database": str(source_path), "backup": str(target), "integrity": info["integrity"],
            "papers": info["papers"] or 0, "bytes": target.stat().st_size}


class RestoreError(RuntimeError):
    """The backup cannot be restored (missing, not a Paper Feed database, corrupt...)."""


def check_restore_source(backup, database=None):
    """Validate *backup* before anything is touched; return its inspection result."""
    backup_path = Path(backup)
    target = Path(database or default_database())
    if not backup_path.is_file():
        raise RestoreError(f"backup file not found: {backup_path}")
    if target.exists() and backup_path.resolve() == target.resolve():
        raise RestoreError("the backup file is the live database itself")
    try:
        info = inspect_database(backup_path)
    except sqlite3.DatabaseError as error:
        raise RestoreError(f"not a readable SQLite database: {backup_path} ({error})") from error
    if info["integrity"] != "ok":
        raise RestoreError(f"integrity_check failed for {backup_path}: {info['integrity']}")
    if info["papers"] is None:
        raise RestoreError(f"{backup_path} is not a Paper Feed database (no papers table)")
    return info


def restore_database(backup, database=None):
    """Replace the live database with *backup* after a safety copy of the current one.

    The caller must make sure no Paper Feed server is using the database.
    Returns {"database", "restored_from", "safety_backup", "papers", "states"}.
    """
    check_restore_source(backup, database)
    target = Path(database or default_database())
    target.parent.mkdir(parents=True, exist_ok=True)
    safety = None
    if target.exists():
        safety = backup_database(target, label="pre-restore")["backup"]
    staging = target.with_name(target.name + ".restore-tmp")
    for leftover in (staging, *(Path(str(staging) + suffix) for suffix in SIDECAR_SUFFIXES)):
        if leftover.exists():
            leftover.unlink()
    _copy_with_backup_api(backup, staging)
    staged = inspect_database(staging)
    if staged["integrity"] != "ok":
        staging.unlink()
        raise RestoreError(f"integrity_check failed for the staged copy: {staged['integrity']}")
    # The safety copy already holds any committed WAL content of the old
    # database; stale -wal/-shm files must not be replayed onto the restored one.
    for suffix in SIDECAR_SUFFIXES:
        sidecar = Path(str(target) + suffix)
        if sidecar.exists():
            sidecar.unlink()
    os.replace(staging, target)
    restored = inspect_database(target)
    return {"database": str(target), "restored_from": str(Path(backup)), "safety_backup": safety,
            "integrity": restored["integrity"], "papers": restored["papers"], "states": restored["states"]}
