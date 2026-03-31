from types import SimpleNamespace

from paper_feed.notion_schema import (
    build_export_claim_properties,
    build_pending_export_query,
    build_export_failure_properties,
    build_stale_export_query,
    build_export_success_properties,
)
from scripts import export_to_zotero


def test_build_export_state_patch_payloads_follow_contract():
    claim = build_export_claim_properties("2026-03-30T10:15:00+00:00")
    success = build_export_success_properties(
        item_key="ABCD1234",
        exported_at="2026-03-30T10:20:00+00:00",
    )
    failure = build_export_failure_properties(
        "Rejected: 状态 must be 收藏 before export",
    )

    assert claim["Zotero 状态"]["select"]["name"] == "导出中"
    assert claim["Export Started At"]["date"]["start"] == "2026-03-30T10:15:00+00:00"
    assert success["Zotero 状态"]["select"]["name"] == "已导出"
    assert success["Zotero Item Key"]["rich_text"][0]["text"]["content"] == "ABCD1234"
    assert success["Export Started At"]["date"] is None
    assert failure["Zotero 状态"]["select"]["name"] == "导出失败"
    assert failure["Export Started At"]["date"] is None


def test_build_export_queries_split_stale_and_fresh_candidates():
    pending = build_pending_export_query(page_size=25)
    stale = build_stale_export_query("2026-03-30T10:00:00+00:00", page_size=25)

    assert pending["filter"]["property"] == "Zotero 状态"
    assert pending["filter"]["select"]["equals"] == "待导出"
    assert pending["sorts"] == [{"timestamp": "last_edited_time", "direction": "ascending"}]
    assert stale["filter"]["and"][0]["select"]["equals"] == "导出中"
    assert stale["sorts"] == [{"timestamp": "last_edited_time", "direction": "ascending"}]


def test_export_dry_run_queries_candidates_without_writes(monkeypatch):
    class _NotionClient:
        def __init__(self, *args, **kwargs):
            self.queries = []

        def query_database(self, database_id, payload):
            self.queries.append((database_id, payload))
            return {
                "results": [
                    {
                        "id": "page-1",
                        "properties": {
                            "标题": {"title": [{"plain_text": "Example Paper"}]},
                            "状态": {"select": {"name": "收藏"}},
                            "Zotero 状态": {"select": {"name": "待导出"}},
                            "paper_id": {"rich_text": [{"plain_text": "doi:10.1177/example"}]},
                            "期刊": {"rich_text": [{"plain_text": "Journal of Marketing"}]},
                            "发布日期": {"date": {"start": "2026-03-30"}},
                            "Canonical URL": {"url": "https://example.org/paper"},
                            "DOI": {"rich_text": [{"plain_text": "10.1177/example"}]},
                            "来源": {"rich_text": [{"plain_text": "journals.sagepub.com"}]},
                            "Authors JSON": {"rich_text": [{"plain_text": "[]"}]},
                        },
                    }
                ]
            }

    monkeypatch.setattr(
        export_to_zotero,
        "IntegrationSettings",
        lambda: SimpleNamespace(
            notion_token="token",
            notion_api_version="2022-06-28",
            notion_papers_database_id="db-1",
            zotero_api_key="z-key",
            zotero_library_type="users",
            zotero_library_id="12345",
            zotero_api_version="3",
        ),
    )
    monkeypatch.setattr(export_to_zotero, "_utc_now", lambda: "2026-03-30T10:20:00+00:00")
    notion_client = _NotionClient()
    monkeypatch.setattr(export_to_zotero, "NotionClient", lambda *args, **kwargs: notion_client)

    preview = export_to_zotero.run_dry_run(limit=10)

    assert preview["count"] == 1
    assert preview["stale_before"] == "2026-03-30T10:00:00+00:00"
    assert preview["items"][0]["page_id"] == "page-1"
    assert preview["items"][0]["paper_id"] == "doi:10.1177/example"


def test_query_export_candidates_paginates_until_exhaustion():
    class _NotionClient:
        def __init__(self):
            self.calls = []

        def query_database(self, database_id, payload):
            self.calls.append(payload)
            if payload["filter"].get("and"):
                if payload.get("start_cursor") == "stale-2":
                    return {"results": [{"id": "stale-3"}], "has_more": False}
                return {"results": [{"id": "stale-1"}, {"id": "stale-2"}], "has_more": True, "next_cursor": "stale-2"}
            if payload.get("start_cursor") == "fresh-2":
                return {"results": [{"id": "fresh-3"}], "has_more": False}
            return {"results": [{"id": "fresh-1"}, {"id": "fresh-2"}], "has_more": True, "next_cursor": "fresh-2"}

    stale_pages, fresh_pages = export_to_zotero._query_export_candidates(
        _NotionClient(),
        "db-1",
        stale_before="2026-03-30T10:00:00+00:00",
        limit=100,
    )

    assert [page["id"] for page in stale_pages] == ["stale-1", "stale-2", "stale-3"]
    assert [page["id"] for page in fresh_pages] == ["fresh-1", "fresh-2", "fresh-3"]
