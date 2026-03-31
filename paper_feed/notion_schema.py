from copy import deepcopy
import json

from paper_feed.categories import topic_names


READING_STATUS_OPTIONS = ["待看", "收藏", "忽略"]
ZOTERO_STATUS_OPTIONS = ["未导出", "待导出", "导出中", "已导出", "导出失败"]
METHOD_OPTIONS = ["Experiment", "Archival", "Theoretical", "Review", "Qualitative", "Unclassified"]
PAGE_BODY_ANCHORS = [
    "SYSTEM_RAW_ABSTRACT",
    "SYSTEM_METADATA_JSON",
    "INGEST_AUDIT",
    "EXPORT_AUDIT",
    "USER_NOTES",
]
AUTHORS_JSON_SENTINEL = "@page-body:SYSTEM_METADATA_JSON"


class SchemaDriftError(ValueError):
    """Raised when an existing Notion database cannot be repaired safely."""


def _option_dicts(names):
    return [{"name": name} for name in names]


def _property_type(definition):
    for key in definition.keys():
        if key not in {"id", "name", "type"}:
            return key
    if "type" in definition:
        return definition["type"]
    raise SchemaDriftError(f"Unable to determine property type for definition: {definition}")


def build_papers_schema():
    return {
        "title": [{"type": "text", "text": {"content": "Papers"}}],
        "properties": {
            "标题": {"title": {}},
            "状态": {"select": {"options": _option_dicts(READING_STATUS_OPTIONS)}},
            "Zotero 状态": {"select": {"options": _option_dicts(ZOTERO_STATUS_OPTIONS)}},
            "标题中文": {"rich_text": {}},
            "研究方法": {"select": {"options": _option_dicts(METHOD_OPTIONS)}},
            "核心话题": {"multi_select": {"options": _option_dicts(topic_names())}},
            "Authors JSON": {"rich_text": {}},
            "发布日期": {"date": {}},
            "期刊": {"rich_text": {}},
            "来源": {"rich_text": {}},
            "Canonical URL": {"url": {}},
            "DOI": {"rich_text": {}},
            "paper_id": {"rich_text": {}},
            "Upstream Fingerprint": {"rich_text": {}},
            "Ingested At": {"date": {}},
            "Last Synced At": {"date": {}},
            "Export Started At": {"date": {}},
            "Zotero Item Key": {"rich_text": {}},
            "Exported At": {"date": {}},
            "Export Error": {"rich_text": {}},
            "人工锁定": {"checkbox": {}},
        },
    }


def build_page_body_anchors():
    blocks = []
    for anchor in PAGE_BODY_ANCHORS:
        blocks.append(
            {
                "object": "block",
                "type": "heading_1",
                "heading_1": {
                    "rich_text": [{"type": "text", "text": {"content": anchor}}],
                },
            }
        )
    return blocks


def build_database_create_payload(parent_page_id):
    schema = build_papers_schema()
    return {
        "parent": {"type": "page_id", "page_id": parent_page_id},
        "title": deepcopy(schema["title"]),
        "properties": deepcopy(schema["properties"]),
    }


def build_database_repair_payload(existing_database):
    schema = build_papers_schema()
    existing_properties = existing_database.get("properties", {})
    repair_properties = {}

    for name, expected_definition in schema["properties"].items():
        current_definition = existing_properties.get(name)
        if current_definition is None:
            repair_properties[name] = deepcopy(expected_definition)
            continue

        expected_type = _property_type(expected_definition)
        current_type = current_definition.get("type") or _property_type(current_definition)
        if current_type != expected_type:
            raise SchemaDriftError(
                f"Property '{name}' has type '{current_type}', expected '{expected_type}'"
            )

        if expected_type not in {"select", "multi_select"}:
            continue

        option_key = expected_type
        current_names = {
            option.get("name")
            for option in current_definition.get(option_key, {}).get("options", [])
            if option.get("name")
        }
        missing = [
            option
            for option in expected_definition[option_key]["options"]
            if option["name"] not in current_names
        ]
        if missing:
            repair_properties[name] = {option_key: {"options": missing}}

    return {"title": deepcopy(schema["title"]), "properties": repair_properties}


def _rich_text(text):
    return {"rich_text": [{"type": "text", "text": {"content": text}}]}


def _select(name):
    return {"select": {"name": name}}


def _date(start):
    return {"date": {"start": start}}


def _multi_select(names):
    return {"multi_select": [{"name": name} for name in names if name]}


def build_paper_lookup_filter(paper_id):
    return {
        "property": "paper_id",
        "rich_text": {"equals": paper_id},
    }


def build_export_candidates_query(stale_before, page_size=100):
    return {
        "page_size": page_size,
        "sorts": [{"timestamp": "last_edited_time", "direction": "ascending"}],
        "filter": {
            "or": [
                {
                    "property": "Zotero 状态",
                    "select": {"equals": "待导出"},
                },
                {
                    "and": [
                        {
                            "property": "Zotero 状态",
                            "select": {"equals": "导出中"},
                        },
                        {
                            "property": "Export Started At",
                            "date": {"before": stale_before},
                        },
                    ]
                },
            ]
        },
    }


def build_pending_export_query(page_size=100, start_cursor=None):
    payload = {
        "page_size": page_size,
        "sorts": [{"timestamp": "last_edited_time", "direction": "ascending"}],
        "filter": {
            "property": "Zotero 状态",
            "select": {"equals": "待导出"},
        },
    }
    if start_cursor:
        payload["start_cursor"] = start_cursor
    return payload


def build_stale_export_query(stale_before, page_size=100, start_cursor=None):
    payload = {
        "page_size": page_size,
        "sorts": [{"timestamp": "last_edited_time", "direction": "ascending"}],
        "filter": {
            "and": [
                {
                    "property": "Zotero 状态",
                    "select": {"equals": "导出中"},
                },
                {
                    "property": "Export Started At",
                    "date": {"before": stale_before},
                },
            ]
        },
    }
    if start_cursor:
        payload["start_cursor"] = start_cursor
    return payload


def build_export_claim_properties(started_at):
    return {
        "Zotero 状态": _select("导出中"),
        "Export Started At": _date(started_at),
    }


def build_export_success_properties(*, item_key, exported_at):
    return {
        "Zotero 状态": _select("已导出"),
        "Zotero Item Key": _rich_text(item_key),
        "Exported At": _date(exported_at),
        "Export Started At": {"date": None},
        "Export Error": {"rich_text": []},
    }


def build_export_failure_properties(message):
    concise = (message or "").strip()[:500]
    return {
        "Zotero 状态": _select("导出失败"),
        "Export Started At": {"date": None},
        "Export Error": _rich_text(concise),
    }


def build_export_rejected_properties(message):
    concise = (message or "").strip()[:500]
    return {
        "Zotero 状态": _select("未导出"),
        "Export Started At": {"date": None},
        "Export Error": _rich_text(concise),
    }


def serialize_authors_json(authors, limit=1800):
    serialized = json.dumps(authors or [], ensure_ascii=False, separators=(",", ":"))
    if len(serialized) > limit:
        return AUTHORS_JSON_SENTINEL
    return serialized


def build_paper_properties_payload(record, *, synced_at, existing_state=None):
    existing_state = existing_state or {}
    if existing_state.get("is_locked"):
        return {
            "Upstream Fingerprint": _rich_text(record.upstream_fingerprint),
            "Last Synced At": _date(synced_at),
        }

    topic_names_list = []
    for topic in record.topics[:3]:
        if isinstance(topic, dict):
            name = topic.get("name", "")
        else:
            name = str(topic)
        if name:
            topic_names_list.append(name)
    if not topic_names_list and record.topic:
        topic_names_list.append(record.topic)

    properties = {
        "标题": {"title": [{"type": "text", "text": {"content": record.title}}]},
        "标题中文": _rich_text(record.title_zh or ""),
        "研究方法": _select(record.method or "Unclassified"),
        "核心话题": _multi_select(topic_names_list[:3]),
        "发布日期": _date(record.published_at) if record.published_at else {"date": None},
        "期刊": _rich_text(record.journal or ""),
        "来源": _rich_text(record.source or ""),
        "Canonical URL": {"url": record.canonical_url or record.link or None},
        "DOI": _rich_text(record.doi or ""),
        "paper_id": _rich_text(record.paper_id),
        "Upstream Fingerprint": _rich_text(record.upstream_fingerprint),
        "Last Synced At": _date(synced_at),
    }

    if not existing_state.get("ingested_at"):
        properties["Ingested At"] = _date(record.ingested_at or synced_at)

    if existing_state.get("zotero_status") != "导出中":
        properties["Authors JSON"] = _rich_text(serialize_authors_json(record.authors))

    return properties


def build_paper_create_payload(database_id, record, *, synced_at, children=None):
    properties = build_paper_properties_payload(record, synced_at=synced_at, existing_state={})
    properties["状态"] = _select("待看")
    properties["Zotero 状态"] = _select("未导出")
    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
    }
    if children:
        payload["children"] = deepcopy(children)
    return payload
