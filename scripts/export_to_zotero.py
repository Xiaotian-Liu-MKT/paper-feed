import sys
from pathlib import Path
import argparse
import json
from datetime import datetime, timedelta, timezone
import json as _json

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from paper_feed.notion_api import NotionClient
from paper_feed.notion_body import (
    MetadataManifestError,
    append_audit_entry,
    collect_anchor_sections,
    resolve_creators_from_metadata_blocks,
)
from paper_feed.notion_schema import AUTHORS_JSON_SENTINEL
from paper_feed.notion_schema import (
    build_export_claim_properties,
    build_export_failure_properties,
    build_export_candidates_query,
    build_pending_export_query,
    build_export_rejected_properties,
    build_stale_export_query,
    build_export_success_properties,
)
from paper_feed.registry import get_registry_entry, load_registry, save_registry, update_registry_namespace
from paper_feed.settings import IntegrationSettings
from paper_feed.zotero_api import ZoteroApiError, ZoteroClient, build_item_payload


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Preview or export Notion favorites to Zotero.")
    parser.add_argument("--dry-run", action="store_true", help="Preview export candidates without mutating state.")
    parser.add_argument("--apply", action="store_true", help="Execute export for eligible Notion pages.")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of candidates to preview.")
    parser.add_argument("--registry-path", default="", help="Optional local registry.json path for machine state.")
    return parser.parse_args(argv)


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _stale_before(now_iso, minutes=20):
    base = datetime.fromisoformat(now_iso)
    return (base - timedelta(minutes=minutes)).isoformat()


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


def summarize_export_candidate(page):
    properties = page.get("properties", {})
    return {
        "page_id": page.get("id", ""),
        "paper_id": _plain_text(properties.get("paper_id", {}).get("rich_text", [])),
        "title": _plain_text(properties.get("标题", {}).get("title", [])),
        "status": (properties.get("状态", {}).get("select") or {}).get("name", ""),
        "zotero_status": (properties.get("Zotero 状态", {}).get("select") or {}).get("name", ""),
        "publication_title": _plain_text(properties.get("期刊", {}).get("rich_text", [])),
        "published_at": (properties.get("发布日期", {}).get("date") or {}).get("start", ""),
        "url": properties.get("Canonical URL", {}).get("url", ""),
        "doi": _plain_text(properties.get("DOI", {}).get("rich_text", [])),
        "source": _plain_text(properties.get("来源", {}).get("rich_text", [])),
        "authors_json": _plain_text(properties.get("Authors JSON", {}).get("rich_text", [])),
        "zotero_item_key": _plain_text(properties.get("Zotero Item Key", {}).get("rich_text", [])),
        "method": (properties.get("研究方法", {}).get("select") or {}).get("name", ""),
        "topics": [item.get("name", "") for item in properties.get("核心话题", {}).get("multi_select", []) if item.get("name")],
    }


def run_dry_run(limit=20):
    settings = IntegrationSettings()
    if not settings.notion_token:
        raise SystemExit("NOTION_TOKEN is required for export dry-run")
    if not settings.notion_papers_database_id:
        raise SystemExit("NOTION_PAPERS_DATABASE_ID is required for export dry-run")

    client = NotionClient(
        settings.notion_token,
        settings.notion_api_version,
        timeout=getattr(settings, "notion_timeout_seconds", 120),
    )
    now_iso = _utc_now()
    query = build_export_candidates_query(
        stale_before=_stale_before(now_iso),
        page_size=min(max(limit, 1), 100),
    )
    pages = client.query_database(settings.notion_papers_database_id, query).get("results", [])
    items = [summarize_export_candidate(page) for page in pages[:limit]]
    return {"count": len(items), "stale_before": query["filter"]["or"][1]["and"][1]["date"]["before"], "items": items}


def _load_creators(candidate, notion_client=None, page_id=""):
    raw = candidate.get("authors_json", "")
    if not raw:
        return []
    if raw == AUTHORS_JSON_SENTINEL:
        if notion_client is None or not page_id:
            raise ValueError("SYSTEM_METADATA_JSON lookup requires Notion client and page_id")
        sections = collect_anchor_sections(list(notion_client.iter_block_children(page_id)))
        metadata_section = sections.get("SYSTEM_METADATA_JSON", {}).get("children", [])
        return resolve_creators_from_metadata_blocks(metadata_section)
    return _json.loads(raw)


def _query_all_pages(notion_client, database_id, query_builder, *, limit):
    page_size = min(max(limit, 1), 100)
    start_cursor = None
    results = []
    while True:
        payload = query_builder(page_size=page_size, start_cursor=start_cursor)
        response = notion_client.query_database(database_id, payload)
        results.extend(response.get("results", []))
        if not response.get("has_more"):
            break
        start_cursor = response.get("next_cursor")
        if not start_cursor:
            break
    return results


def _query_export_candidates(notion_client, database_id, *, stale_before, limit):
    stale_pages = _query_all_pages(
        notion_client,
        database_id,
        lambda **kwargs: build_stale_export_query(stale_before=stale_before, **kwargs),
        limit=limit,
    )
    fresh_pages = _query_all_pages(
        notion_client,
        database_id,
        build_pending_export_query,
        limit=limit,
    )
    return stale_pages, fresh_pages


def _build_payload_from_candidate(candidate, page_id, notion_client):
    return build_item_payload(
        title=candidate["title"],
        creators=_load_creators(candidate, notion_client=notion_client, page_id=page_id),
        publication_title=candidate["publication_title"],
        published_at=candidate["published_at"],
        url=candidate["url"],
        doi=candidate["doi"],
        paper_id=candidate["paper_id"],
        notion_page_id=page_id,
        source=candidate["source"],
        method=candidate.get("method", ""),
        topics=candidate.get("topics", []),
    )


def _tags_contain_machine_id(item, paper_id):
    machine_tag = f"pf:id:{paper_id}"
    for entry in item.get("data", {}).get("tags", []):
        if entry.get("tag") == machine_tag:
            return True
    for entry in item.get("tags", []):
        if entry.get("tag") == machine_tag:
            return True
    return False


def _append_export_audit(notion_client, page_id, message, *, occurred_at):
    if not all(
        hasattr(notion_client, method_name)
        for method_name in ("iter_block_children", "append_block_children", "delete_block")
    ):
        return
    append_audit_entry(
        notion_client,
        page_id,
        "EXPORT_AUDIT",
        message,
        occurred_at=occurred_at,
    )


def run_apply(limit=20, registry_path=None):
    settings = IntegrationSettings()
    if not settings.notion_token:
        raise SystemExit("NOTION_TOKEN is required for export apply")
    if not settings.notion_papers_database_id:
        raise SystemExit("NOTION_PAPERS_DATABASE_ID is required for export apply")
    if not settings.zotero_api_key:
        raise SystemExit("ZOTERO_API_KEY is required for export apply")
    if not settings.zotero_library_id:
        raise SystemExit("ZOTERO_LIBRARY_ID is required for export apply")

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
    registry = load_registry(registry_path)
    now_iso = _utc_now()
    stale_before = _stale_before(now_iso)
    stale_pages, fresh_pages = _query_export_candidates(
        notion_client,
        settings.notion_papers_database_id,
        stale_before=stale_before,
        limit=limit,
    )
    pages = stale_pages[:limit]
    if len(pages) < limit:
        pages.extend(fresh_pages[: max(0, limit - len(pages))])
    summary = {"processed": 0, "claimed": 0, "exported": 0, "failed": 0, "rejected": 0, "reconciled": 0, "pending_writeback": 0}

    try:
        for index, page in enumerate(pages[:limit]):
            summary["processed"] += 1
            candidate = summarize_export_candidate(page)
            page_id = candidate["page_id"]
            paper_id = candidate["paper_id"]
            registry_entry = get_registry_entry(registry, paper_id) or {}
            export_entry = registry_entry.get("export") or {}
            notion_item_key = candidate.get("zotero_item_key", "")

            if candidate["zotero_status"] == "导出中":
                item_key = notion_item_key or export_entry.get("zotero_item_key", "")
                if not item_key and paper_id:
                    recovered = zotero_client.find_item_by_tag(f"pf:id:{paper_id}")
                    if recovered:
                        item_key = recovered.get("key", "")
                if item_key:
                    notion_client.update_page(
                        page_id,
                        {"properties": build_export_success_properties(item_key=item_key, exported_at=now_iso)},
                    )
                    update_registry_namespace(
                        registry,
                        paper_id,
                        "export",
                        {
                            "zotero_item_key": item_key,
                            "last_export_attempt_at": now_iso,
                            "last_export_terminal_state": "已导出",
                        },
                    )
                    summary["reconciled"] += 1
                    continue
                notion_client.update_page(
                    page_id,
                    {"properties": build_export_failure_properties("Aborted before Zotero write; retry required")},
                )
                _append_export_audit(
                    notion_client,
                    page_id,
                    "stale_export_failed: Aborted before Zotero write; retry required",
                    occurred_at=now_iso,
                )
                update_registry_namespace(
                    registry,
                    paper_id,
                    "export",
                    {
                        "last_export_attempt_at": now_iso,
                        "last_export_terminal_state": "导出失败",
                    },
                )
                summary["failed"] += 1
                continue

            if candidate["zotero_status"] == "待导出" and candidate["status"] != "收藏":
                notion_client.update_page(
                    page_id,
                    {"properties": build_export_rejected_properties("Rejected: 状态 must be 收藏 before export")},
                )
                summary["rejected"] += 1
                continue

            if not paper_id:
                notion_client.update_page(
                    page_id,
                    {"properties": build_export_failure_properties("Missing paper_id; export requires canonical record")},
                )
                summary["failed"] += 1
                continue

            notion_client.update_page(page_id, {"properties": build_export_claim_properties(now_iso)})
            summary["claimed"] += 1

            try:
                payload = _build_payload_from_candidate(candidate, page_id, notion_client)
                existing_item_key = notion_item_key or export_entry.get("zotero_item_key", "")
                if existing_item_key:
                    existing_item = zotero_client.retrieve_item(existing_item_key)
                    expected_version = existing_item.get("version") or existing_item.get("data", {}).get("version")
                    try:
                        response = zotero_client.update_item(existing_item_key, payload, expected_version=expected_version)
                    except ZoteroApiError as error:
                        if error.status_code != 412:
                            raise
                        refetched = zotero_client.retrieve_item(existing_item_key)
                        if not _tags_contain_machine_id(refetched, paper_id):
                            raise
                        retry_version = refetched.get("version") or refetched.get("data", {}).get("version")
                        response = zotero_client.update_item(existing_item_key, payload, expected_version=retry_version)
                    item_key = response.get("successful", {}).get("0", {}).get("key", existing_item_key) or existing_item_key
                else:
                    response = zotero_client.create_item(payload)
                    item_key = response["successful"]["0"]["key"]
                update_registry_namespace(
                    registry,
                    paper_id,
                    "export",
                    {
                        "zotero_item_key": item_key,
                        "last_export_attempt_at": now_iso,
                        "last_export_terminal_state": "writeback_pending",
                    },
                )
                try:
                    notion_client.update_page(
                        page_id,
                        {"properties": build_export_success_properties(item_key=item_key, exported_at=now_iso)},
                    )
                    update_registry_namespace(
                        registry,
                        paper_id,
                        "export",
                        {
                            "zotero_item_key": item_key,
                            "last_export_attempt_at": now_iso,
                            "last_export_terminal_state": "已导出",
                        },
                    )
                    summary["exported"] += 1
                except Exception:
                    summary["pending_writeback"] += 1
            except Exception as error:
                notion_client.update_page(
                    page_id,
                    {"properties": build_export_failure_properties(str(error))},
                )
                _append_export_audit(
                    notion_client,
                    page_id,
                    f"export_failed: {error}",
                    occurred_at=now_iso,
                )
                update_registry_namespace(
                    registry,
                    paper_id,
                    "export",
                    {
                        "last_export_attempt_at": now_iso,
                        "last_export_terminal_state": "导出失败",
                    },
                )
                summary["failed"] += 1
    finally:
        save_registry(registry_path, registry)

    return summary


def main(argv=None):
    args = _parse_args(argv)
    if args.dry_run:
        print(json.dumps({"mode": "dry-run", **run_dry_run(limit=args.limit)}, ensure_ascii=True, indent=2))
        return 0
    if args.apply:
        print(
            json.dumps(
                {"mode": "apply", **run_apply(limit=args.limit, registry_path=args.registry_path or None)},
                ensure_ascii=True,
                indent=2,
            )
        )
        return 0
    raise SystemExit("Use either --dry-run or --apply")


if __name__ == "__main__":
    raise SystemExit(main())
