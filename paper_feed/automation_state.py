import csv
import json
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

from paper_feed.registry import empty_registry, merge_registry_entry


STATE_BRANCH = "automation-state"


def empty_heartbeat(now_iso="", workflow=""):
    return {"last_keepalive": now_iso, "workflow": workflow}


def bootstrap_state_tree(root, *, now_iso="", touch_heartbeat=False, workflow=""):
    root_path = Path(root)
    state_root = root_path / "state"
    created = []

    for directory in (
        state_root,
        state_root / "duplicate_audit",
        state_root / "compatibility",
    ):
        directory.mkdir(parents=True, exist_ok=True)

    registry_path = state_root / "registry.json"
    if not registry_path.exists():
        registry_path.write_text(json.dumps(empty_registry(), ensure_ascii=False, indent=2), encoding="utf-8")
        created.append(str(registry_path))

    heartbeat_path = state_root / "heartbeat.json"
    if touch_heartbeat or not heartbeat_path.exists():
        heartbeat_path.write_text(
            json.dumps(empty_heartbeat(now_iso, workflow), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        created.append(str(heartbeat_path))

    unresolved_json_path = state_root / "legacy_unresolved.json"
    if not unresolved_json_path.exists():
        unresolved_json_path.write_text("[]\n", encoding="utf-8")
        created.append(str(unresolved_json_path))

    unresolved_csv_path = state_root / "legacy_unresolved.csv"
    if not unresolved_csv_path.exists():
        with unresolved_csv_path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["paper_id", "reason"])
        created.append(str(unresolved_csv_path))

    return {
        "root": str(root_path),
        "state_branch": STATE_BRANCH,
        "created_or_updated": created,
    }


def _utc_audit_stamp(occurred_at):
    parsed = datetime.fromisoformat(str(occurred_at).replace("Z", "+00:00")).astimezone(timezone.utc)
    return parsed.strftime("%Y%m%dT%H%M%SZ")


def write_duplicate_audit_report(registry_path, *, occurred_at, workflow, entries, degraded_reason=""):
    if not registry_path:
        return ""
    if not entries and not degraded_reason:
        return ""

    registry_file = Path(registry_path)
    duplicate_root = registry_file.parent / "duplicate_audit"
    duplicate_root.mkdir(parents=True, exist_ok=True)

    payload = {
        "generated_at": occurred_at,
        "workflow": workflow,
        "degraded_reason": degraded_reason,
        "entry_count": len(entries),
        "entries": entries,
    }
    output_path = duplicate_root / f"{workflow}-{_utc_audit_stamp(occurred_at)}.json"
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(output_path)


def merge_registry_namespace(remote_registry, local_registry, namespace):
    remote_registry = deepcopy(remote_registry or empty_registry())
    local_registry = deepcopy(local_registry or empty_registry())

    merged = {
        "metadata": deepcopy(remote_registry.get("metadata") or local_registry.get("metadata") or empty_registry()["metadata"]),
        "papers": {},
    }

    remote_papers = remote_registry.get("papers", {})
    local_papers = local_registry.get("papers", {})
    for paper_id in sorted(set(remote_papers) | set(local_papers)):
        remote_entry = remote_papers.get(paper_id)
        local_entry = local_papers.get(paper_id)
        if remote_entry and local_entry:
            if namespace in local_entry:
                merged["papers"][paper_id] = merge_registry_entry(remote_entry, local_entry, namespace)
            else:
                merged["papers"][paper_id] = deepcopy(remote_entry)
            continue
        if local_entry:
            merged["papers"][paper_id] = deepcopy(local_entry)
            continue
        merged["papers"][paper_id] = deepcopy(remote_entry)

    return merged
