import json

from paper_feed.models import CanonicalPaperRecord
from paper_feed.notion_schema import (
    build_export_candidates_query,
    build_paper_create_payload,
    build_paper_lookup_filter,
    build_paper_properties_payload,
)


def _record():
    return CanonicalPaperRecord(
        paper_id="doi:10.1177/example",
        source_id="legacy-1",
        title="Example Paper",
        title_zh="示例论文",
        method="Experiment",
        topic="AI与营销技术",
        topics=[{"name": "AI与营销技术", "confidence": 0.9}],
        authors=[
            {
                "full_name": "Xiaotian Li",
                "given_name": "Xiaotian",
                "family_name": "Li",
            }
        ],
        link="https://example.org/paper",
        canonical_url="https://example.org/paper",
        journal="Journal of Marketing",
        source="journals.sagepub.com",
        published_at="2026-03-30T00:00:00+00:00",
        doi="10.1177/example",
        raw_abstract="Original abstract",
        raw_abstract_source="crossref",
        upstream_fingerprint="fingerprint-1",
        ingested_at="2026-03-30T10:00:00+00:00",
        classification_version="v2",
    )


def test_build_paper_lookup_filter_is_exact_rich_text_query():
    payload = build_paper_lookup_filter("doi:10.1177/example")

    assert payload == {
        "property": "paper_id",
        "rich_text": {"equals": "doi:10.1177/example"},
    }


def test_build_paper_create_payload_initializes_statuses():
    payload = build_paper_create_payload("db-123", _record(), synced_at="2026-03-30T10:05:00+00:00")
    props = payload["properties"]

    assert payload["parent"] == {"database_id": "db-123"}
    assert props["状态"]["select"]["name"] == "待看"
    assert props["Zotero 状态"]["select"]["name"] == "未导出"
    assert props["Ingested At"]["date"]["start"] == "2026-03-30T10:00:00+00:00"
    assert props["Last Synced At"]["date"]["start"] == "2026-03-30T10:05:00+00:00"


def test_build_paper_create_payload_passes_children_through():
    payload = build_paper_create_payload(
        "db-123",
        _record(),
        synced_at="2026-03-30T10:05:00+00:00",
        children=[{"object": "block", "type": "heading_1", "heading_1": {"rich_text": [{"type": "text", "text": {"content": "SYSTEM_RAW_ABSTRACT"}}]}}],
    )

    assert "children" in payload
    assert payload["children"][0]["heading_1"]["rich_text"][0]["text"]["content"] == "SYSTEM_RAW_ABSTRACT"


def test_build_paper_properties_payload_for_locked_page_only_updates_safe_fields():
    props = build_paper_properties_payload(
        _record(),
        synced_at="2026-03-30T10:05:00+00:00",
        existing_state={
            "is_locked": True,
            "zotero_status": "未导出",
            "ingested_at": "2026-03-30T10:00:00+00:00",
        },
    )

    assert set(props.keys()) == {"Upstream Fingerprint", "Last Synced At"}


def test_build_paper_properties_payload_skips_authors_during_export_in_progress():
    props = build_paper_properties_payload(
        _record(),
        synced_at="2026-03-30T10:05:00+00:00",
        existing_state={
            "is_locked": False,
            "zotero_status": "导出中",
            "ingested_at": "2026-03-30T10:00:00+00:00",
        },
    )

    assert "Authors JSON" not in props
    assert "Canonical URL" in props


def test_build_export_candidates_query_targets_fresh_and_stale_pages():
    query = build_export_candidates_query(stale_before="2026-03-30T09:40:00+00:00")

    assert query["page_size"] == 100
    assert query["filter"]["or"][0]["property"] == "Zotero 状态"
    assert query["filter"]["or"][1]["and"][0]["property"] == "Zotero 状态"


def test_build_paper_properties_payload_serializes_authors_json():
    props = build_paper_properties_payload(
        _record(),
        synced_at="2026-03-30T10:05:00+00:00",
        existing_state={
            "is_locked": False,
            "zotero_status": "未导出",
            "ingested_at": "",
        },
    )

    serialized = props["Authors JSON"]["rich_text"][0]["text"]["content"]
    assert json.loads(serialized)[0]["family_name"] == "Li"
