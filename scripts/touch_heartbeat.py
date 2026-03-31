import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_feed.automation_state import bootstrap_state_tree


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Update automation-state heartbeat.json for keepalive.")
    parser.add_argument("--root", required=True, help="Target checkout root for the automation-state branch.")
    parser.add_argument("--timestamp", required=True, help="UTC ISO 8601 timestamp for last_keepalive.")
    parser.add_argument("--workflow", default="keepalive-state", help="Workflow name recorded in heartbeat.json.")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    result = bootstrap_state_tree(
        args.root,
        now_iso=args.timestamp,
        touch_heartbeat=True,
        workflow=args.workflow,
    )
    print(json.dumps(result, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
