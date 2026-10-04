"""Command line: `python -m paper_feed [import-legacy|backup] ...`.

A bare `python -m paper_feed [--root R] [--database D] [--dry-run]` keeps its
historical meaning (one-way legacy JSON/XML import).
"""
import argparse
import json
import sys

from .backup import PROJECT_DIR, backup_database
from .importer import LegacyImporter


def _configure_stdio():
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _add_import_options(parser):
    parser.add_argument("--root", default=str(PROJECT_DIR),
                        help="Project directory containing legacy files (default: the Paper Feed project directory).")
    parser.add_argument("--database", help="SQLite database path (default: <root>/data/paper_feed.sqlite3).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Import into a temporary shadow database and report counts without changing anything.")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m paper_feed",
        description="Paper Feed storage utilities. Without a subcommand, runs import-legacy (backward compatible).")
    _add_import_options(parser)
    commands = parser.add_subparsers(dest="command", metavar="{import-legacy,backup}")
    import_parser = commands.add_parser(
        "import-legacy", help="Import pre-SQLite files (filtered_feed.xml, web/*.json) into SQLite (idempotent).",
        description="One-way, idempotent import of legacy XML/JSON files into the SQLite database.")
    _add_import_options(import_parser)
    backup_parser = commands.add_parser(
        "backup", help="Write a consistent copy of the SQLite database using the sqlite3 backup API.",
        description="Create a consistent backup of the SQLite database (safe while the server is running).")
    backup_parser.add_argument("--out", metavar="DIR",
                               help="Directory for the backup file (default: the database's own data/ directory).")
    backup_parser.add_argument("--database",
                               help="Database to back up (default: PAPER_FEED_DB or data/paper_feed.sqlite3).")
    return parser


def main(argv=None):
    _configure_stdio()
    args = build_parser().parse_args(argv)
    if args.command == "backup":
        try:
            result = backup_database(args.database, args.out)
        except FileNotFoundError as error:
            print(f"Error: {error}", file=sys.stderr)
            return 1
        print(json.dumps(result, indent=2))
        return 0 if result["integrity"] == "ok" else 1
    print(json.dumps(LegacyImporter(args.root, args.database).run(args.dry_run), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
