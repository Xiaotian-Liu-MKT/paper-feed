"""`python -m paper_feed <command>` - see paper_feed/cli.py (`--help` lists commands).

Without a subcommand this prints help; the legacy import is `import-legacy`.
"""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())
