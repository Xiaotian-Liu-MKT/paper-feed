from paper_feed.notion_schema import (
    PAGE_BODY_ANCHORS,
    build_database_repair_payload,
    build_page_body_anchors,
    build_papers_schema,
)


def test_build_papers_schema_contains_required_properties_and_options():
    schema = build_papers_schema()
    props = schema["properties"]

    assert "标题" in props
    assert "状态" in props
    assert "Zotero 状态" in props
    assert "paper_id" in props
    assert "Export Started At" in props

    assert props["状态"]["select"]["options"] == [
        {"name": "待看"},
        {"name": "收藏"},
        {"name": "忽略"},
    ]
    assert props["Zotero 状态"]["select"]["options"] == [
        {"name": "未导出"},
        {"name": "待导出"},
        {"name": "导出中"},
        {"name": "已导出"},
        {"name": "导出失败"},
    ]
    assert props["研究方法"]["select"]["options"][-1] == {"name": "Unclassified"}


def test_build_page_body_anchors_uses_exact_heading_order():
    anchors = build_page_body_anchors()

    assert [block["heading_1"]["rich_text"][0]["text"]["content"] for block in anchors] == PAGE_BODY_ANCHORS


def test_build_database_repair_payload_adds_missing_properties_and_options():
    existing = {
        "properties": {
            "标题": {"id": "title", "type": "title", "title": {}},
            "状态": {
                "id": "state",
                "type": "select",
                "select": {"options": [{"name": "待看"}]},
            },
            "Zotero 状态": {
                "id": "zotero",
                "type": "select",
                "select": {"options": [{"name": "未导出"}]},
            },
        }
    }

    payload = build_database_repair_payload(existing)

    assert "标题中文" in payload["properties"]
    assert payload["properties"]["状态"]["select"]["options"] == [
        {"name": "收藏"},
        {"name": "忽略"},
    ]
    assert payload["properties"]["Zotero 状态"]["select"]["options"] == [
        {"name": "待导出"},
        {"name": "导出中"},
        {"name": "已导出"},
        {"name": "导出失败"},
    ]
