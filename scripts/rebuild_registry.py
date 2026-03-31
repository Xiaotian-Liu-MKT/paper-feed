import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_feed.registry import empty_registry, save_registry, update_registry_namespace
from paper_feed.settings import IntegrationSettings
from paper_feed.notion_api import NotionClient
from paper_feed.zotero_api import ZoteroClient


def _plain_text(property_value):
    if not property_value:
        return ""
    for item in property_value:
        text = item.get("plain_text")
        if text is not None:
            return text
        nested = item.get("text", {}).get("content")
        if nested is not None:
            return nested
    return ""


def iter_notion_pages(client, database_id, *, page_size=100):
    start_cursor = None
    while True:
        payload = {"page_size": page_size}
        if start_cursor:
            payload["start_cursor"] = start_cursor
        response = client.query_database(database_id, payload)
        for result in response.get("results", []):
            yield result
        if not response.get("has_more"):
            break
        start_cursor = response.get("next_cursor")
        if not start_cursor:
            break


def extract_notion_registry_seed(page):
    properties = page.get("properties", {})
    return {
        "paper_id": _plain_text(properties.get("paper_id", {}).get("rich_text", [])),
        "notion_page_id": page.get("id", ""),
        "upstream_fingerprint": _plain_text(properties.get("Upstream Fingerprint", {}).get("rich_text", [])),
    }


def extract_zotero_registry_seed(item):
    tags = item.get("data", {}).get("tags", []) or item.get("tags", [])
    paper_id = ""
    for entry in tags:
        tag = entry.get("tag", "")
        if tag.startswith("pf:id:"):
            paper_id = tag[len("pf:id:") :]
            break
    return {
        "paper_id": paper_id,
        "zotero_item_key": item.get("key", "") or item.get("data", {}).get("key", ""),
    }


def rebuild_registry(notion_client, zotero_client, database_id):
    registry = empty_registry()
    notion_seen = {}
    zotero_seen = {}
    collisions = []
    summary = {
        "notion_pages_scanned": 0,
        "zotero_items_scanned": 0,
        "skipped_notion_missing_paper_id": 0,
        "skipped_zotero_missing_machine_tag": 0,
    }

    for page in iter_notion_pages(notion_client, database_id):
        summary["notion_pages_scanned"] += 1
        seed = extract_notion_registry_seed(page)
        paper_id = seed["paper_id"]
        if not paper_id:
            summary["skipped_notion_missing_paper_id"] += 1
            continue
        if paper_id in notion_seen and notion_seen[paper_id] != seed["notion_page_id"]:
            collisions.append(
                {
                    "source": "notion",
                    "paper_id": paper_id,
                    "page_ids": [notion_seen[paper_id], seed["notion_page_id"]],
                }
            )
            continue
        notion_seen[paper_id] = seed["notion_page_id"]
        update_registry_namespace(
            registry,
            paper_id,
            "ingest",
            {
                "notion_page_id": seed["notion_page_id"],
                "upstream_fingerprint": seed["upstream_fingerprint"],
            },
        )

    for item in zotero_client.iter_items(limit=100):
        summary["zotero_items_scanned"] += 1
        seed = extract_zotero_registry_seed(item)
        paper_id = seed["paper_id"]
        if not paper_id:
            summary["skipped_zotero_missing_machine_tag"] += 1
            continue
        if paper_id in zotero_seen and zotero_seen[paper_id] != seed["zotero_item_key"]:
            collisions.append(
                {
                    "source": "zotero",
                    "paper_id": paper_id,
                    "item_keys": [zotero_seen[paper_id], seed["zotero_item_key"]],
                }
            )
            continue
        zotero_seen[paper_id] = seed["zotero_item_key"]
        update_registry_namespace(
            registry,
            paper_id,
            "export",
            {
                "zotero_item_key": seed["zotero_item_key"],
            },
        )

    summary["registry_entries"] = len(registry["papers"])
    summary["collision_count"] = len(collisions)
    return {"registry": registry, "summary": summary, "collisions": collisions}


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Rebuild automation-state registry from Notion and Zotero.")
    parser.add_argument("--dry-run", action="store_true", help="Print the rebuilt registry summary without writing.")
    parser.add_argument("--apply", action="store_true", help="Write the rebuilt registry to --registry-path.")
    parser.add_argument("--registry-path", default="", help="Path to automation-state state/registry.json.")
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    if args.dry_run == args.apply:
        raise SystemExit("Use either --dry-run or --apply")

    settings = IntegrationSettings()
    if not settings.notion_token:
        raise SystemExit("NOTION_TOKEN is required for rebuild_registry")
    if not settings.notion_papers_database_id:
        raise SystemExit("NOTION_PAPERS_DATABASE_ID is required for rebuild_registry")
    if not settings.zotero_api_key:
        raise SystemExit("ZOTERO_API_KEY is required for rebuild_registry")
    if not settings.zotero_library_id:
        raise SystemExit("ZOTERO_LIBRARY_ID is required for rebuild_registry")
    if args.apply and not args.registry_path:
        raise SystemExit("--registry-path is required in apply mode")

    notion_client = NotionClient(
        settings.notion_token,
        settings.notion_api_version,
        timeout=getattr(settings, "notion_timeout_seconds", 120),
    )
    zotero_client = ZoteroClient(
        settings.zotero_api_key,
        settings.zotero_library_type,
        settings.zotero_library_id,
        settings.zotero_api_version,
    )
    zotero_client.validate_access()
    payload = rebuild_registry(notion_client, zotero_client, settings.notion_papers_database_id)
    if payload["collisions"]:
        raise SystemExit(
            "rebuild_registry detected duplicate reachable records; resolve collisions before applying\n"
            + json.dumps(payload["collisions"], ensure_ascii=True, indent=2)
        )
    if args.apply:
        save_registry(args.registry_path, payload["registry"])
    print(
        json.dumps(
            {
                "mode": "apply" if args.apply else "dry-run",
                **payload["summary"],
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
