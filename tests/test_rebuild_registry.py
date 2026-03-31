from types import SimpleNamespace

from paper_feed.registry import load_registry
from scripts import rebuild_registry


def test_rebuild_registry_combines_notion_and_zotero_records(tmp_path):
    class _NotionClient:
        def query_database(self, database_id, payload):
            return {
                "results": [
                    {
                        "id": "page-1",
                        "properties": {
                            "paper_id": {"rich_text": [{"plain_text": "doi:10.1177/example"}]},
                            "Upstream Fingerprint": {"rich_text": [{"plain_text": "fingerprint-1"}]},
                        },
                    }
                ],
                "has_more": False,
            }

    class _ZoteroClient:
        def iter_items(self, *, limit=100):
            yield {
                "key": "ITEM1234",
                "data": {"tags": [{"tag": "pf:id:doi:10.1177/example"}]},
            }

    payload = rebuild_registry.rebuild_registry(_NotionClient(), _ZoteroClient(), "db-1")

    assert payload["summary"]["registry_entries"] == 1
    assert payload["registry"]["papers"]["doi:10.1177/example"]["ingest"]["notion_page_id"] == "page-1"
    assert payload["registry"]["papers"]["doi:10.1177/example"]["export"]["zotero_item_key"] == "ITEM1234"


def test_rebuild_registry_apply_fails_on_collisions_without_writing(tmp_path, monkeypatch):
    registry_path = tmp_path / "state" / "registry.json"

    class _NotionClient:
        def query_database(self, database_id, payload):
            return {
                "results": [
                    {
                        "id": "page-1",
                        "properties": {"paper_id": {"rich_text": [{"plain_text": "doi:10.1177/example"}]}},
                    },
                    {
                        "id": "page-2",
                        "properties": {"paper_id": {"rich_text": [{"plain_text": "doi:10.1177/example"}]}},
                    },
                ],
                "has_more": False,
            }

    class _ZoteroClient:
        def validate_access(self):
            return True

        def iter_items(self, *, limit=100):
            return iter([])

    monkeypatch.setattr(
        rebuild_registry,
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
    monkeypatch.setattr(rebuild_registry, "NotionClient", lambda *args, **kwargs: _NotionClient())
    monkeypatch.setattr(rebuild_registry, "ZoteroClient", lambda *args, **kwargs: _ZoteroClient())

    try:
        rebuild_registry.main(["--apply", "--registry-path", str(registry_path)])
    except SystemExit as error:
        assert "duplicate reachable records" in str(error)
    else:
        raise AssertionError("Expected collision rebuild to abort")

    assert not registry_path.exists()
