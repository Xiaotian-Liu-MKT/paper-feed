import json
from pathlib import Path


def empty_registry():
    return {
        "metadata": {
            "schema_version": 1,
            "last_compaction_week": "",
        },
        "papers": {},
    }


def load_registry(path):
    if path is None:
        return empty_registry()
    registry_path = Path(path)
    if not registry_path.exists():
        return empty_registry()
    with registry_path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    payload.setdefault("metadata", {"schema_version": 1, "last_compaction_week": ""})
    payload.setdefault("papers", {})
    return payload


def save_registry(path, registry):
    if path is None:
        return
    registry_path = Path(path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = registry_path.with_suffix(registry_path.suffix + ".tmp")
    with temp_path.open("w", encoding="utf-8") as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2)
    temp_path.replace(registry_path)


def get_registry_entry(registry, paper_id):
    return registry.get("papers", {}).get(paper_id)


def update_registry_namespace(registry, paper_id, namespace, values):
    registry.setdefault("papers", {})
    paper_entry = registry["papers"].setdefault(paper_id, {"paper_id": paper_id})
    paper_entry["paper_id"] = paper_id
    paper_entry.setdefault(namespace, {})
    paper_entry[namespace].update(values)
    return paper_entry


def merge_registry_entry(remote, local, owned_namespace):
    merged = dict(remote)
    merged["paper_id"] = local["paper_id"]
    merged[owned_namespace] = dict(local.get(owned_namespace, {}))
    return merged
