import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_feed.automation_state import merge_registry_namespace
from paper_feed.registry import empty_registry


def _load_registry(path):
    registry_path = Path(path)
    if not registry_path.exists():
        return empty_registry()
    with registry_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Merge one owned registry namespace against the latest automation-state copy.")
    parser.add_argument("--remote", required=True, help="Path to the remote registry snapshot.")
    parser.add_argument("--local", required=True, help="Path to the local registry snapshot.")
    parser.add_argument("--namespace", required=True, choices=["ingest", "export"], help="Owned namespace to merge.")
    parser.add_argument("--output", required=True, help="Output path for the merged registry.")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    merged = merge_registry_namespace(
        _load_registry(args.remote),
        _load_registry(args.local),
        args.namespace,
    )
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"namespace": args.namespace, "output": str(output_path)}, ensure_ascii=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
