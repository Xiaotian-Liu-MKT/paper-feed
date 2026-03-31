import argparse
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from get_RSS import (
    get_existing_items,
    load_abstracts,
    load_categories,
    load_translations,
    load_user_corrections,
    normalize_label_entries,
    pick_primary,
)
from paper_feed.automation_state import write_duplicate_audit_report
from paper_feed.canonical import build_canonical_record
from paper_feed.models import CanonicalPaperRecord
from paper_feed.notion_api import NotionApiError, NotionClient
from paper_feed.notion_body import (
    MetadataChunkLimitError,
    append_audit_entry,
    build_initial_page_body_blocks,
    build_metadata_blocks,
    build_raw_abstract_blocks,
    ensure_page_body_anchors,
    build_audit_entry_block,
    rewrite_anchor_section,
)
from paper_feed.notion_schema import (
    PAGE_BODY_ANCHORS,
    build_export_candidates_query,
    build_paper_create_payload,
    build_paper_lookup_filter,
    build_paper_properties_payload,
    serialize_authors_json,
)
from paper_feed.registry import get_registry_entry, load_registry, save_registry, update_registry_namespace
from paper_feed.settings import IntegrationSettings


def _valid_names(categories, key):
    return {
        item.get("name")
        for item in categories.get(key, [])
        if isinstance(item, dict) and item.get("name")
    }


def load_canonical_records():
    items = get_existing_items()
    translation_cache = load_translations()
    abstract_cache = load_abstracts()
    categories = load_categories() or {}
    user_corrections = load_user_corrections()
    valid_methods = _valid_names(categories, "methods")
    valid_topics = _valid_names(categories, "topics")

    records = []
    for item in items:
        raw_title = item["title"]
        item_id = item["id"]
        abstract_info = abstract_cache.get(item_id, {})
        cache_data = translation_cache.get(raw_title, {})

        title_zh = ""
        methods = []
        topics = []
        classification_version = ""

        if isinstance(cache_data, dict):
            title_zh = cache_data.get("zh", "")
            methods = normalize_label_entries(
                cache_data.get("methods", cache_data.get("method", "")),
                valid_methods,
            )
            topics = normalize_label_entries(
                cache_data.get("topics", cache_data.get("topic", "")),
                valid_topics,
            )
            classification_version = cache_data.get("classification_version", "")
        elif isinstance(cache_data, str):
            title_zh = cache_data

        correction = user_corrections.get(item_id, {})
        if isinstance(correction, dict) and correction:
            corrected_methods = normalize_label_entries(correction.get("methods", []), valid_methods)
            corrected_topics = normalize_label_entries(correction.get("topics", []), valid_topics)
            if corrected_methods:
                methods = corrected_methods
            if corrected_topics:
                topics = corrected_topics

        record = build_canonical_record(
            item,
            {
                "zh": title_zh,
                "methods": methods,
                "topics": topics,
                "classification_version": classification_version,
            },
            abstract_info,
        )
        record.method = pick_primary(methods, "Unclassified")
        record.topic = pick_primary(topics, "Other Marketing")
        records.append(record)

    return records


def _slice_records(records, *, offset=0, limit=None):
    if offset:
        records = records[offset:]
    if limit is not None:
        records = records[:limit]
    return records


def render_dry_run(records, limit=None, offset=0):
    limited_records = _slice_records(records, offset=offset, limit=limit)
    preview = []
    for record in limited_records:
        preview.append(
            {
                "paper_id": record.paper_id,
                "title": record.title,
                "status": "待看",
                "zotero_status": "未导出",
                "method": record.method,
                "topic": record.topic,
            }
        )
    return preview


def _utc_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def parse_paper_id_filter(value):
    if not value:
        return set()
    if isinstance(value, set):
        return {item.strip() for item in value if isinstance(item, str) and item.strip()}
    return {line.strip() for line in str(value).splitlines() if line.strip()}


def extract_existing_state(page):
    properties = page.get("properties", {})
    return {
        "is_locked": bool(properties.get("人工锁定", {}).get("checkbox")),
        "zotero_status": (properties.get("Zotero 状态", {}).get("select") or {}).get("name", ""),
        "ingested_at": (properties.get("Ingested At", {}).get("date") or {}).get("start", ""),
        "zotero_item_key": _plain_text(properties.get("Zotero Item Key", {}).get("rich_text", [])),
        "upstream_fingerprint": _plain_text(properties.get("Upstream Fingerprint", {}).get("rich_text", [])),
        "authors_json": _plain_text(properties.get("Authors JSON", {}).get("rich_text", [])),
    }


def sync_page_body(client, page_id, record, *, existing_state=None):
    existing_state = existing_state or {}
    if existing_state.get("is_locked"):
        return {"updated_sections": []}
    if not all(
        hasattr(client, method_name)
        for method_name in ("iter_block_children", "append_block_children", "delete_block")
    ):
        return {"updated_sections": []}
    sections, repairs = ensure_page_body_anchors(client, page_id, return_repairs=True)
    if repairs:
        client.append_block_children(
            page_id,
            [build_audit_entry_block(repair, occurred_at=_utc_now()) for repair in repairs],
            after=sections["INGEST_AUDIT"]["anchor"]["id"],
        )
    updated_sections = []

    raw_abstract_blocks = build_raw_abstract_blocks(record.raw_abstract, record.raw_abstract_source)
    rewrite_anchor_section(client, page_id, "SYSTEM_RAW_ABSTRACT", raw_abstract_blocks)
    updated_sections.append("SYSTEM_RAW_ABSTRACT")

    if existing_state.get("zotero_status") != "导出中":
        try:
            metadata_blocks = build_metadata_blocks(record.authors)
        except MetadataChunkLimitError as error:
            append_audit_entry(
                client,
                page_id,
                "INGEST_AUDIT",
                f"authors_too_large: {error}",
                occurred_at=_utc_now(),
            )
            updated_sections.append("INGEST_AUDIT")
        else:
            rewrite_anchor_section(client, page_id, "SYSTEM_METADATA_JSON", metadata_blocks)
            updated_sections.append("SYSTEM_METADATA_JSON")

    return {"updated_sections": updated_sections}


def _plain_text(property_value):
    if not property_value:
        return ""
    for item in property_value:
        if item.get("plain_text") is not None:
            return item["plain_text"]
        content = item.get("text", {}).get("content")
        if content is not None:
            return content
    return ""


def run_apply(limit=None, registry_path=None, paper_id_filter=None, force_metadata_repair=False, offset=0):
    settings = IntegrationSettings()
    if not settings.notion_token:
        raise SystemExit("NOTION_TOKEN is required for apply mode")
    if not settings.notion_papers_database_id:
        raise SystemExit("NOTION_PAPERS_DATABASE_ID is required for apply mode")
    paper_id_filter = parse_paper_id_filter(paper_id_filter)
    if force_metadata_repair and not paper_id_filter:
        raise SystemExit("FORCE_METADATA_REPAIR requires an explicit paper_id_filter")

    client = NotionClient(
        settings.notion_token,
        settings.notion_api_version,
        timeout=getattr(settings, "notion_timeout_seconds", 120),
    )
    records = load_canonical_records()
    if paper_id_filter:
        records = [record for record in records if record.paper_id in paper_id_filter]
    records = _slice_records(records, offset=offset, limit=limit)
    registry = load_registry(registry_path)

    summary = {"processed": 0, "created": 0, "updated": 0, "duplicates": 0}
    exact_lookup_count = 0
    duplicate_entries = []
    duplicate_degraded_reason = ""
    duplicate_report_time = _utc_now()

    try:
        for record in records:
            summary["processed"] += 1
            synced_at = _utc_now()
            duplicate_report_time = synced_at
            registry_entry = get_registry_entry(registry, record.paper_id) or {}
            notion_page_id = ((registry_entry.get("ingest") or {}).get("notion_page_id", "")).strip()
            if notion_page_id:
                try:
                    existing_page = client.retrieve_page(notion_page_id)
                except NotionApiError as error:
                    if error.status_code != 404:
                        raise
                else:
                    existing_state = extract_existing_state(existing_page)
                    if (
                        existing_state.get("is_locked")
                        and existing_state.get("upstream_fingerprint")
                        and existing_state.get("upstream_fingerprint") != record.upstream_fingerprint
                        and all(
                            hasattr(client, method_name)
                            for method_name in ("iter_block_children", "append_block_children", "delete_block")
                        )
                    ):
                        append_audit_entry(
                            client,
                            notion_page_id,
                            "INGEST_AUDIT",
                            "locked_metadata_changed: upstream metadata changed while 人工锁定=true",
                            occurred_at=synced_at,
                        )
                    if force_metadata_repair:
                        if existing_state.get("is_locked"):
                            raise SystemExit("FORCE_METADATA_REPAIR requires 人工锁定=false")
                        if existing_state.get("zotero_status") not in {"未导出", "导出失败"}:
                            raise SystemExit("FORCE_METADATA_REPAIR requires Zotero 状态 to be 未导出 or 导出失败")
                        if existing_state.get("zotero_item_key"):
                            raise SystemExit("FORCE_METADATA_REPAIR refuses pages that already have Zotero Item Key")
                    client.update_page(
                        notion_page_id,
                        {
                            "properties": build_paper_properties_payload(
                                record,
                                synced_at=synced_at,
                                existing_state=existing_state,
                            )
                        },
                    )
                    if (
                        existing_state.get("zotero_status") == "导出中"
                        and existing_state.get("authors_json")
                        and existing_state.get("authors_json") != serialize_authors_json(record.authors)
                        and all(
                            hasattr(client, method_name)
                            for method_name in ("iter_block_children", "append_block_children", "delete_block")
                        )
                    ):
                        append_audit_entry(
                            client,
                            notion_page_id,
                            "INGEST_AUDIT",
                            "author_drift_deferred: Zotero 状态=导出中, metadata rewrite skipped",
                            occurred_at=synced_at,
                        )
                    sync_page_body(client, notion_page_id, record, existing_state=existing_state)
                    summary["updated"] += 1
                    update_registry_namespace(
                        registry,
                        record.paper_id,
                        "ingest",
                        {
                            "notion_page_id": notion_page_id,
                            "upstream_fingerprint": record.upstream_fingerprint,
                            "last_seen_at": synced_at,
                        },
                    )
                    continue

            exact_lookup_count += 1
            if exact_lookup_count > 100:
                duplicate_degraded_reason = "exact_lookup_safeguard_exceeded"
                raise SystemExit(
                    "Exact paper_id lookup safeguard exceeded 100 records in one ingest run; "
                    "registry-backed resolution is required before continuing"
                )
            query_payload = {"page_size": 2, "filter": build_paper_lookup_filter(record.paper_id)}
            response = client.query_database(settings.notion_papers_database_id, query_payload)
            results = response.get("results", [])

            if len(results) > 1:
                summary["duplicates"] += 1
                duplicate_entries.append(
                    {
                        "paper_id": record.paper_id,
                        "title": record.title,
                        "sample_page_ids": [item.get("id", "") for item in results],
                        "observed_result_count": len(results),
                        "has_more": bool(response.get("has_more")),
                        "reason": "duplicate_exact_match",
                    }
                )
                continue

            if not results:
                initial_children = build_initial_page_body_blocks(
                    record.raw_abstract,
                    record.raw_abstract_source,
                    record.authors,
                    occurred_at=synced_at,
                )
                created = client.create_page(
                    build_paper_create_payload(
                        settings.notion_papers_database_id,
                        record,
                        synced_at=synced_at,
                        children=initial_children,
                    )
                )
                if initial_children is None:
                    sync_page_body(
                        client,
                        created.get("id", ""),
                        record,
                        existing_state={"is_locked": False, "zotero_status": "未导出"},
                    )
                summary["created"] += 1
                update_registry_namespace(
                    registry,
                    record.paper_id,
                    "ingest",
                    {
                        "notion_page_id": created.get("id", ""),
                        "upstream_fingerprint": record.upstream_fingerprint,
                        "last_seen_at": synced_at,
                    },
                )
                continue

            existing_page = results[0]
            existing_state = extract_existing_state(existing_page)
            if (
                existing_state.get("is_locked")
                and existing_state.get("upstream_fingerprint")
                and existing_state.get("upstream_fingerprint") != record.upstream_fingerprint
                and all(
                    hasattr(client, method_name)
                    for method_name in ("iter_block_children", "append_block_children", "delete_block")
                )
            ):
                append_audit_entry(
                    client,
                    existing_page["id"],
                    "INGEST_AUDIT",
                    "locked_metadata_changed: upstream metadata changed while 人工锁定=true",
                    occurred_at=synced_at,
                )
            if force_metadata_repair:
                if existing_state.get("is_locked"):
                    raise SystemExit("FORCE_METADATA_REPAIR requires 人工锁定=false")
                if existing_state.get("zotero_status") not in {"未导出", "导出失败"}:
                    raise SystemExit("FORCE_METADATA_REPAIR requires Zotero 状态 to be 未导出 or 导出失败")
                if existing_state.get("zotero_item_key"):
                    raise SystemExit("FORCE_METADATA_REPAIR refuses pages that already have Zotero Item Key")
            properties = build_paper_properties_payload(
                record,
                synced_at=synced_at,
                existing_state=existing_state,
            )
            client.update_page(existing_page["id"], {"properties": properties})
            if (
                existing_state.get("zotero_status") == "导出中"
                and existing_state.get("authors_json")
                and existing_state.get("authors_json") != serialize_authors_json(record.authors)
                and all(
                    hasattr(client, method_name)
                    for method_name in ("iter_block_children", "append_block_children", "delete_block")
                )
            ):
                append_audit_entry(
                    client,
                    existing_page["id"],
                    "INGEST_AUDIT",
                    "author_drift_deferred: Zotero 状态=导出中, metadata rewrite skipped",
                    occurred_at=synced_at,
                )
            sync_page_body(client, existing_page["id"], record, existing_state=existing_state)
            summary["updated"] += 1
            update_registry_namespace(
                registry,
                record.paper_id,
                "ingest",
                {
                    "notion_page_id": existing_page["id"],
                    "upstream_fingerprint": record.upstream_fingerprint,
                    "last_seen_at": synced_at,
                },
            )
    finally:
        save_registry(registry_path, registry)
        write_duplicate_audit_report(
            registry_path,
            occurred_at=duplicate_report_time,
            workflow="ingest-to-notion",
            entries=duplicate_entries,
            degraded_reason=duplicate_degraded_reason,
        )

    return summary


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Preview or ingest canonical papers into Notion.")
    parser.add_argument("--dry-run", action="store_true", help="Preview Notion rows without mutating anything.")
    parser.add_argument("--apply", action="store_true", help="Write canonical rows into Notion.")
    parser.add_argument("--registry-path", default="", help="Optional local registry.json path for machine state.")
    parser.add_argument(
        "--paper-id-filter",
        default="",
        help="Optional newline-delimited canonical paper_id values to limit apply mode.",
    )
    parser.add_argument(
        "--force-metadata-repair",
        action="store_true",
        help="Rebuild metadata blocks for the filtered pages only.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="Maximum number of preview rows to print in dry-run mode.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Number of canonical records to skip before preview/apply processing.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = _parse_args(argv)
    if args.dry_run:
        records = load_canonical_records()
        preview = render_dry_run(records, limit=args.limit, offset=args.offset)
        print(
            json.dumps(
                {
                    "mode": "dry-run",
                    "count": len(records),
                    "preview_count": len(preview),
                    "limit": args.limit,
                    "offset": args.offset,
                    "items": preview,
                },
                ensure_ascii=True,
                indent=2,
            )
        )
        return 0

    if args.apply:
        summary = run_apply(
            limit=args.limit,
            registry_path=args.registry_path or None,
            paper_id_filter=args.paper_id_filter,
            force_metadata_repair=args.force_metadata_repair,
            offset=args.offset,
        )
        print(json.dumps({"mode": "apply", **summary}, ensure_ascii=True, indent=2))
        return 0

    export_query = build_export_candidates_query(stale_before=_utc_now())
    raise SystemExit(
        "Choose either --dry-run or --apply. Export query foundation is available for later use: "
        + json.dumps(export_query, ensure_ascii=True)
    )


if __name__ == "__main__":
    raise SystemExit(main())
