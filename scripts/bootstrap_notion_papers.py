import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_feed.notion_api import NotionApiError, NotionClient
from paper_feed.notion_schema import (
    PAGE_BODY_ANCHORS,
    build_database_create_payload,
    build_database_repair_payload,
)
from paper_feed.settings import IntegrationSettings


def build_bootstrap_plan(parent_page_id, database_id):
    return {
        "mode": "dry-run",
        "action": "validate_or_repair" if database_id else "create_if_missing",
        "parent_page_id": parent_page_id,
        "database_id": database_id,
        "anchors": PAGE_BODY_ANCHORS,
    }


def _structured_probe_error(stage, error):
    error_type = "notion_api_error"
    if error.status_code == 401:
        error_type = "authentication_failed"
    elif stage == "read_parent" and error.status_code in {403, 404}:
        error_type = "missing_parent_access"
    elif stage == "create_scratch" and error.status_code in {403, 404}:
        error_type = "missing_create_content_scope"
    elif stage == "archive_scratch" and error.status_code in {403, 404}:
        error_type = "missing_archive_scope"
    return {
        "mode": "apply",
        "action": "capability_probe_failed",
        "error": {
            "type": error_type,
            "stage": stage,
            "status_code": error.status_code,
            "code": error.code,
            "message": error.notion_message or str(error),
        },
    }


def _find_existing_child_database(client, parent_page_id, title):
    matches = []
    for block in client.iter_block_children(parent_page_id):
        if block.get("type") != "child_database":
            continue
        child = block.get("child_database", {})
        if child.get("title") == title and block.get("id"):
            matches.append(block["id"])
    if len(matches) > 1:
        raise SystemExit(
            "Multiple child databases named 'Papers' were found under the parent page; "
            "set NOTION_PAPERS_DATABASE_ID explicitly before applying bootstrap changes"
        )
    return matches[0] if matches else ""


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Bootstrap or validate the Notion Papers database.")
    parser.add_argument("--apply", action="store_true", help="Apply the bootstrap plan to Notion.")
    parser.add_argument("--database-id", default="", help="Existing Notion database ID to validate/repair.")
    parser.add_argument(
        "--create-if-missing",
        action="store_true",
        help="Allow create mode when no database ID is configured.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    settings = IntegrationSettings()
    parent_page_id = settings.notion_parent_page_id.strip()
    database_id = (args.database_id or settings.notion_papers_database_id).strip()

    plan = build_bootstrap_plan(parent_page_id, database_id)
    if not args.apply:
        print(json.dumps(plan, ensure_ascii=True, indent=2))
        return 0

    if not parent_page_id:
        raise SystemExit("NOTION_PARENT_PAGE_ID is required when applying bootstrap changes")
    if not settings.notion_token:
        raise SystemExit("NOTION_TOKEN is required when applying bootstrap changes")
    if not database_id and not args.create_if_missing:
        raise SystemExit(
            "Refusing to create a new Notion database without an explicit --create-if-missing flag"
        )

    client = NotionClient(
        settings.notion_token,
        settings.notion_api_version,
        timeout=getattr(settings, "notion_timeout_seconds", 120),
    )
    probe = {"parent_page_id": parent_page_id, "scratch_page_id": ""}
    try:
        client.retrieve_page(parent_page_id)
    except NotionApiError as error:
        print(json.dumps(_structured_probe_error("read_parent", error), ensure_ascii=True, indent=2))
        return 2

    try:
        scratch = client.create_page(
            {
                "parent": {"type": "page_id", "page_id": parent_page_id},
                "properties": {
                    "title": [
                        {
                            "type": "text",
                            "text": {"content": "Paper Feed bootstrap probe"},
                        }
                    ]
                },
            }
        )
        probe["scratch_page_id"] = scratch["id"]
    except NotionApiError as error:
        print(json.dumps(_structured_probe_error("create_scratch", error), ensure_ascii=True, indent=2))
        return 2

    try:
        client.archive_page(probe["scratch_page_id"])
    except NotionApiError as error:
        print(json.dumps(_structured_probe_error("archive_scratch", error), ensure_ascii=True, indent=2))
        return 2

    if database_id:
        database = client.retrieve_database(database_id)
        repair_payload = build_database_repair_payload(database)
        if repair_payload["properties"]:
            client.update_database(database_id, repair_payload)
            action = "repaired"
        else:
            action = "validated"
        resolved_id = database_id
    else:
        resolved_id = _find_existing_child_database(client, parent_page_id, "Papers")
        if resolved_id:
            database = client.retrieve_database(resolved_id)
            repair_payload = build_database_repair_payload(database)
            if repair_payload["properties"]:
                client.update_database(resolved_id, repair_payload)
                action = "reused_and_repaired"
            else:
                action = "reused_existing"
        else:
            created = client.create_database(build_database_create_payload(parent_page_id))
            resolved_id = created["id"]
            action = "created"

    print(
        json.dumps(
            {
                "mode": "apply",
                "action": action,
                "database_id": resolved_id,
                "probe": probe,
                "anchors": PAGE_BODY_ANCHORS,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
