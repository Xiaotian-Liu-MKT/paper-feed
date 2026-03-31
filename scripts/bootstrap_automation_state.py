import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_feed.automation_state import bootstrap_state_tree


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Bootstrap the automation-state layout.")
    parser.add_argument("--root", required=True, help="Target checkout root for the automation-state branch.")
    parser.add_argument("--timestamp", default="", help="Optional UTC timestamp for heartbeat.json.")
    parser.add_argument("--touch-heartbeat", action="store_true", help="Rewrite heartbeat.json even when it already exists.")
    parser.add_argument("--workflow", default="", help="Optional workflow name to write into heartbeat.json when touching it.")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    result = bootstrap_state_tree(
        args.root,
        now_iso=args.timestamp,
        touch_heartbeat=args.touch_heartbeat,
        workflow=args.workflow,
    )
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
