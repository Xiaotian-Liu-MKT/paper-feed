"""Consistent online backups of the Paper Feed SQLite database."""
import os
import sqlite3
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]


def default_database():
    return os.environ.get("PAPER_FEED_DB") or str(PROJECT_DIR / "data" / "paper_feed.sqlite3")


def backup_database(database=None, out_dir=None):
    """Copy *database* with the sqlite3 backup API (safe while the server runs).

    The default name `paper_feed.sqlite3-backup-<timestamp>.sqlite3` in `data/`
    matches the repository's `data/paper_feed.sqlite3-*` ignore rule.
    """
    source_path = Path(database or default_database())
    if not source_path.exists():
        raise FileNotFoundError(f"database not found: {source_path}")
    target_dir = Path(out_dir) if out_dir else source_path.parent
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"paper_feed.sqlite3-backup-{datetime.now():%Y%m%dT%H%M%S%f}.sqlite3"

    source = sqlite3.connect(str(source_path))
    try:
        destination = sqlite3.connect(str(target))
        try:
            source.backup(destination)
            check = destination.execute("PRAGMA integrity_check").fetchone()[0]
            papers = destination.execute(
                "SELECT count(*) FROM sqlite_master WHERE type='table' AND name='papers'").fetchone()[0]
            paper_count = destination.execute("SELECT count(*) FROM papers").fetchone()[0] if papers else 0
        finally:
            destination.close()
    finally:
        source.close()
    return {"database": str(source_path), "backup": str(target), "integrity": check,
            "papers": paper_count, "bytes": target.stat().st_size}
