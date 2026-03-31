from pathlib import Path
from types import SimpleNamespace

from paper_feed.notion_body import build_metadata_blocks
from paper_feed.notion_schema import AUTHORS_JSON_SENTINEL, PAGE_BODY_ANCHORS
from paper_feed.registry import load_registry
from paper_feed.zotero_api import ZoteroApiError
from scripts import export_to_zotero


def _page(
    *,
    page_id="page-1",
    status="收藏",
    zotero_status="待导出",
    paper_id="doi:10.1177/example",
    title="Example Paper",
):
    return {
        "id": page_id,
        "properties": {
            "标题": {"title": [{"plain_text": title}]},
            "状态": {"select": {"name": status}},
            "Zotero 状态": {"select": {"name": zotero_status}},
            "paper_id": {"rich_text": [{"plain_text": paper_id}]},
            "期刊": {"rich_text": [{"plain_text": "Journal of Marketing"}]},
            "发布日期": {"date": {"start": "2026-03-30"}},
            "Canonical URL": {"url": "https://example.org/paper"},
            "DOI": {"rich_text": [{"plain_text": "10.1177/example"}]},
            "来源": {"rich_text": [{"plain_text": "journals.sagepub.com"}]},
            "Authors JSON": {
                "rich_text": [
                    {
                        "plain_text": '[{"full_name":"Xiaotian Li","family_name":"Li","given_name":"Xiaotian"}]'
                    }
                ]
            },
            "Zotero Item Key": {"rich_text": []},
            "研究方法": {"select": {"name": "Experiment"}},
            "核心话题": {"multi_select": [{"name": "AI与营销技术"}]},
        },
    }


def test_export_apply_claims_then_marks_success_and_updates_registry(tmp_path, monkeypatch):
    registry_path = tmp_path / "state" / "registry.json"

    class _NotionClient:
        def __init__(self, *args, **kwargs):
            self.updates = []

        def query_database(self, database_id, payload):
            status = payload["filter"]
            if "and" in status:
                return {"results": []}
            return {"results": [_page()]}

        def update_page(self, page_id, payload):
            self.updates.append((page_id, payload))
            return {"id": page_id}

    class _ZoteroClient:
        def __init__(self, *args, **kwargs):
            self.payloads = []

        def validate_access(self):
            return True

        def create_item(self, payload):
            self.payloads.append(payload)
            return {"successful": {"0": {"key": "ITEM1234"}}}

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
    monkeypatch.setattr(export_to_zotero, "NotionClient", lambda *args, **kwargs: _NotionClient())
    monkeypatch.setattr(export_to_zotero, "ZoteroClient", lambda *args, **kwargs: _ZoteroClient())
    monkeypatch.setattr(export_to_zotero, "_utc_now", lambda: "2026-03-30T10:20:00+00:00")

    result = export_to_zotero.run_apply(limit=10, registry_path=registry_path)
    registry = load_registry(registry_path)

    assert result["claimed"] == 1
    assert result["exported"] == 1
    assert registry["papers"]["doi:10.1177/example"]["export"]["zotero_item_key"] == "ITEM1234"


def test_export_apply_rejects_non_favorite_requests_without_zotero_write(tmp_path, monkeypatch):
    registry_path = tmp_path / "state" / "registry.json"

    class _NotionClient:
        def __init__(self, *args, **kwargs):
            self.updates = []

        def query_database(self, database_id, payload):
            status = payload["filter"]
            if "and" in status:
                return {"results": []}
            return {"results": [_page(status="待看")]}

        def update_page(self, page_id, payload):
            self.updates.append((page_id, payload))
            return {"id": page_id}

    class _ZoteroClient:
        def __init__(self, *args, **kwargs):
            self.called = False

        def validate_access(self):
            return True

        def create_item(self, payload):
            self.called = True
            raise AssertionError("create_item should not run for rejected export requests")

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
    notion_client = _NotionClient()
    monkeypatch.setattr(export_to_zotero, "NotionClient", lambda *args, **kwargs: notion_client)
    monkeypatch.setattr(export_to_zotero, "ZoteroClient", _ZoteroClient)
    monkeypatch.setattr(export_to_zotero, "_utc_now", lambda: "2026-03-30T10:20:00+00:00")

    result = export_to_zotero.run_apply(limit=10, registry_path=registry_path)

    assert result["rejected"] == 1
    assert notion_client.updates[0][1]["properties"]["Zotero 状态"]["select"]["name"] == "未导出"


def test_export_apply_reconciles_stale_export_before_fresh_candidates(tmp_path, monkeypatch):
    registry_path = tmp_path / "state" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        '{"metadata":{"schema_version":1,"last_compaction_week":""},"papers":{"doi:10.1177/stale":{"paper_id":"doi:10.1177/stale","export":{"zotero_item_key":"ITEM-ST"}}}}',
        encoding="utf-8",
    )

    stale_page = _page(page_id="page-stale", zotero_status="导出中", paper_id="doi:10.1177/stale")
    fresh_page = _page(page_id="page-fresh", zotero_status="待导出", paper_id="doi:10.1177/fresh")

    class _NotionClient:
        def __init__(self, *args, **kwargs):
            self.updates = []

        def query_database(self, database_id, payload):
            if payload["filter"].get("and"):
                return {"results": [stale_page]}
            return {"results": [fresh_page]}

        def update_page(self, page_id, payload):
            self.updates.append((page_id, payload))
            return {"id": page_id}

    class _ZoteroClient:
        def __init__(self, *args, **kwargs):
            pass

        def validate_access(self):
            return True

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
    notion_client = _NotionClient()
    monkeypatch.setattr(export_to_zotero, "NotionClient", lambda *args, **kwargs: notion_client)
    monkeypatch.setattr(export_to_zotero, "ZoteroClient", _ZoteroClient)
    monkeypatch.setattr(export_to_zotero, "_utc_now", lambda: "2026-03-30T10:20:00+00:00")

    result = export_to_zotero.run_apply(limit=1, registry_path=registry_path)

    assert result["reconciled"] == 1
    assert result["processed"] == 1
    assert notion_client.updates[0][0] == "page-stale"


def test_export_apply_leaves_page_in_progress_when_notion_success_write_fails(tmp_path, monkeypatch):
    registry_path = tmp_path / "state" / "registry.json"

    class _NotionClient:
        def __init__(self, *args, **kwargs):
            self.updates = []

        def query_database(self, database_id, payload):
            if payload["filter"].get("and"):
                return {"results": []}
            return {"results": [_page()]}

        def update_page(self, page_id, payload):
            self.updates.append((page_id, payload))
            if len(self.updates) == 2:
                raise RuntimeError("Notion write-back failed after Zotero success")
            return {"id": page_id}

    class _ZoteroClient:
        def __init__(self, *args, **kwargs):
            pass

        def validate_access(self):
            return True

        def create_item(self, payload):
            return {"successful": {"0": {"key": "ITEM1234"}}}

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
    monkeypatch.setattr(export_to_zotero, "NotionClient", lambda *args, **kwargs: _NotionClient())
    monkeypatch.setattr(export_to_zotero, "ZoteroClient", lambda *args, **kwargs: _ZoteroClient())
    monkeypatch.setattr(export_to_zotero, "_utc_now", lambda: "2026-03-30T10:20:00+00:00")

    result = export_to_zotero.run_apply(limit=1, registry_path=registry_path)
    registry = load_registry(registry_path)

    assert result["pending_writeback"] == 1
    assert registry["papers"]["doi:10.1177/example"]["export"]["zotero_item_key"] == "ITEM1234"
    assert registry["papers"]["doi:10.1177/example"]["export"]["last_export_terminal_state"] == "writeback_pending"


def test_export_apply_recovers_stale_page_by_machine_tag_when_registry_missing(tmp_path, monkeypatch):
    registry_path = tmp_path / "state" / "registry.json"
    stale_page = _page(page_id="page-stale", zotero_status="导出中", paper_id="doi:10.1177/stale-tag")

    class _NotionClient:
        def __init__(self, *args, **kwargs):
            self.updates = []

        def query_database(self, database_id, payload):
            if payload["filter"].get("and"):
                return {"results": [stale_page]}
            return {"results": []}

        def update_page(self, page_id, payload):
            self.updates.append((page_id, payload))
            return {"id": page_id}

    class _ZoteroClient:
        def __init__(self, *args, **kwargs):
            pass

        def validate_access(self):
            return True

        def find_item_by_tag(self, tag):
            assert tag == "pf:id:doi:10.1177/stale-tag"
            return {"key": "ITEM-BY-TAG"}

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
    monkeypatch.setattr(export_to_zotero, "NotionClient", lambda *args, **kwargs: _NotionClient())
    monkeypatch.setattr(export_to_zotero, "ZoteroClient", lambda *args, **kwargs: _ZoteroClient())
    monkeypatch.setattr(export_to_zotero, "_utc_now", lambda: "2026-03-30T10:20:00+00:00")

    result = export_to_zotero.run_apply(limit=1, registry_path=registry_path)
    registry = load_registry(registry_path)

    assert result["reconciled"] == 1
    assert registry["papers"]["doi:10.1177/stale-tag"]["export"]["zotero_item_key"] == "ITEM-BY-TAG"


def test_export_apply_validates_zotero_before_claiming_pages(tmp_path, monkeypatch):
    registry_path = tmp_path / "state" / "registry.json"

    class _NotionClient:
        def __init__(self, *args, **kwargs):
            self.updates = []

        def query_database(self, database_id, payload):
            return {"results": [_page()]}

        def update_page(self, page_id, payload):
            self.updates.append((page_id, payload))
            return {"id": page_id}

    class _ZoteroClient:
        def __init__(self, *args, **kwargs):
            pass

        def validate_access(self):
            raise RuntimeError("Zotero unavailable")

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
    notion_client = _NotionClient()
    monkeypatch.setattr(export_to_zotero, "NotionClient", lambda *args, **kwargs: notion_client)
    monkeypatch.setattr(export_to_zotero, "ZoteroClient", lambda *args, **kwargs: _ZoteroClient())

    try:
        export_to_zotero.run_apply(limit=1, registry_path=registry_path)
    except RuntimeError as error:
        assert "Zotero unavailable" in str(error)
    else:
        raise AssertionError("Expected Zotero dependency validation to fail before claiming pages")

    assert notion_client.updates == []


def test_export_apply_retries_existing_item_update_once_on_412(tmp_path, monkeypatch):
    registry_path = tmp_path / "state" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        '{"metadata":{"schema_version":1,"last_compaction_week":""},"papers":{"doi:10.1177/example":{"paper_id":"doi:10.1177/example","export":{"zotero_item_key":"ITEM-EXIST"}}}}',
        encoding="utf-8",
    )

    class _NotionClient:
        def __init__(self, *args, **kwargs):
            self.updates = []

        def query_database(self, database_id, payload):
            if payload["filter"].get("and"):
                return {"results": []}
            return {"results": [_page()]}

        def update_page(self, page_id, payload):
            self.updates.append((page_id, payload))
            return {"id": page_id}

    class _ZoteroClient:
        def __init__(self, *args, **kwargs):
            self.update_calls = []

        def validate_access(self):
            return True

        def retrieve_item(self, item_key):
            return {"key": item_key, "version": 7, "tags": [{"tag": "pf:id:doi:10.1177/example"}]}

        def update_item(self, item_key, payload, expected_version=None):
            self.update_calls.append(expected_version)
            if len(self.update_calls) == 1:
                raise ZoteroApiError("conflict", status_code=412)
            return {"successful": {"0": {"key": item_key}}}

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
    monkeypatch.setattr(export_to_zotero, "NotionClient", lambda *args, **kwargs: _NotionClient())
    zotero_client = _ZoteroClient()
    monkeypatch.setattr(export_to_zotero, "ZoteroClient", lambda *args, **kwargs: zotero_client)
    monkeypatch.setattr(export_to_zotero, "_utc_now", lambda: "2026-03-30T10:20:00+00:00")

    result = export_to_zotero.run_apply(limit=1, registry_path=registry_path)

    assert result["exported"] == 1
    assert zotero_client.update_calls == [7, 7]


def test_export_apply_reads_creators_from_page_body_when_authors_are_chunked(tmp_path, monkeypatch):
    registry_path = tmp_path / "state" / "registry.json"
    creators = [{"full_name": "Xiaotian Li", "family_name": "Li", "given_name": "Xiaotian"}]
    metadata_blocks = build_metadata_blocks(creators, inline_limit=10, chunk_size=10)

    sentinel_page = _page()
    sentinel_page["properties"]["Authors JSON"]["rich_text"][0]["plain_text"] = AUTHORS_JSON_SENTINEL

    class _NotionClient:
        def __init__(self, *args, **kwargs):
            self.updates = []

        def query_database(self, database_id, payload):
            if payload["filter"].get("and"):
                return {"results": []}
            return {"results": [sentinel_page]}

        def update_page(self, page_id, payload):
            self.updates.append((page_id, payload))
            return {"id": page_id}

        def iter_block_children(self, block_id, *, page_size=100):
            blocks = []
            counter = 0
            for anchor in PAGE_BODY_ANCHORS:
                counter += 1
                blocks.append(
                    {
                        "id": f"block-{counter}",
                        "type": "heading_1",
                        "heading_1": {"rich_text": [{"plain_text": anchor, "text": {"content": anchor}}]},
                    }
                )
                if anchor == "SYSTEM_METADATA_JSON":
                    for block in metadata_blocks:
                        counter += 1
                        blocks.append(
                            {
                                "id": f"block-{counter}",
                                "type": "code",
                                "code": block["code"],
                            }
                        )
            return blocks

    class _ZoteroClient:
        def __init__(self, *args, **kwargs):
            self.payloads = []

        def validate_access(self):
            return True

        def create_item(self, payload):
            self.payloads.append(payload)
            return {"successful": {"0": {"key": "ITEM1234"}}}

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
    notion_client = _NotionClient()
    zotero_client = _ZoteroClient()
    monkeypatch.setattr(export_to_zotero, "NotionClient", lambda *args, **kwargs: notion_client)
    monkeypatch.setattr(export_to_zotero, "ZoteroClient", lambda *args, **kwargs: zotero_client)
    monkeypatch.setattr(export_to_zotero, "_utc_now", lambda: "2026-03-30T10:20:00+00:00")

    result = export_to_zotero.run_apply(limit=1, registry_path=registry_path)

    assert result["exported"] == 1
    assert zotero_client.payloads[0]["creators"] == [{"creatorType": "author", "firstName": "Xiaotian", "lastName": "Li"}]


def test_export_apply_prefers_notion_zotero_item_key_when_registry_is_missing(tmp_path, monkeypatch):
    registry_path = tmp_path / "state" / "registry.json"
    page = _page()
    page["properties"]["Zotero Item Key"]["rich_text"] = [{"plain_text": "ITEM-NOTION"}]

    class _NotionClient:
        def __init__(self, *args, **kwargs):
            self.updates = []

        def query_database(self, database_id, payload):
            if payload["filter"].get("and"):
                return {"results": []}
            return {"results": [page]}

        def update_page(self, page_id, payload):
            self.updates.append((page_id, payload))
            return {"id": page_id}

    class _ZoteroClient:
        def __init__(self, *args, **kwargs):
            self.update_calls = []
            self.create_called = False

        def validate_access(self):
            return True

        def retrieve_item(self, item_key):
            assert item_key == "ITEM-NOTION"
            return {"key": item_key, "version": 3, "tags": [{"tag": "pf:id:doi:10.1177/example"}]}

        def update_item(self, item_key, payload, expected_version=None):
            self.update_calls.append((item_key, expected_version))
            return {"successful": {"0": {"key": item_key}}}

        def create_item(self, payload):
            self.create_called = True
            raise AssertionError("create_item should not run when Notion already has Zotero Item Key")

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
    monkeypatch.setattr(export_to_zotero, "NotionClient", lambda *args, **kwargs: _NotionClient())
    zotero_client = _ZoteroClient()
    monkeypatch.setattr(export_to_zotero, "ZoteroClient", lambda *args, **kwargs: zotero_client)
    monkeypatch.setattr(export_to_zotero, "_utc_now", lambda: "2026-03-30T10:20:00+00:00")

    result = export_to_zotero.run_apply(limit=1, registry_path=registry_path)

    assert result["exported"] == 1
    assert zotero_client.update_calls == [("ITEM-NOTION", 3)]


def test_export_apply_audits_metadata_manifest_failure(tmp_path, monkeypatch):
    registry_path = tmp_path / "state" / "registry.json"
    page = _page()
    page["properties"]["Authors JSON"]["rich_text"][0]["plain_text"] = AUTHORS_JSON_SENTINEL

    class _NotionClient:
        def __init__(self, *args, **kwargs):
            self.updates = []
            self.children = []
            self._counter = 0
            for anchor in PAGE_BODY_ANCHORS:
                self._append_existing(
                    {
                        "object": "block",
                        "type": "heading_1",
                        "heading_1": {"rich_text": [{"type": "text", "text": {"content": anchor}}]},
                    }
                )

        def _append_existing(self, block):
            self._counter += 1
            existing = dict(block)
            existing["id"] = f"block-{self._counter}"
            self.children.append(existing)

        def query_database(self, database_id, payload):
            if payload["filter"].get("and"):
                return {"results": []}
            return {"results": [page]}

        def update_page(self, page_id, payload):
            self.updates.append((page_id, payload))
            return {"id": page_id}

        def iter_block_children(self, block_id, *, page_size=100):
            return list(self.children)

        def append_block_children(self, block_id, children, after=None):
            inserted = []
            for child in children:
                self._counter += 1
                block = dict(child)
                block["id"] = f"block-{self._counter}"
                inserted.append(block)
            index = len(self.children) if after is None else next(
                i for i, block in enumerate(self.children) if block["id"] == after
            ) + 1
            self.children[index:index] = inserted
            return {"results": inserted}

        def delete_block(self, block_id):
            self.children = [block for block in self.children if block["id"] != block_id]
            return {"id": block_id}

    class _ZoteroClient:
        def __init__(self, *args, **kwargs):
            pass

        def validate_access(self):
            return True

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
    notion_client = _NotionClient()
    monkeypatch.setattr(export_to_zotero, "NotionClient", lambda *args, **kwargs: notion_client)
    monkeypatch.setattr(export_to_zotero, "ZoteroClient", lambda *args, **kwargs: _ZoteroClient())
    monkeypatch.setattr(export_to_zotero, "_utc_now", lambda: "2026-03-30T10:20:00+00:00")

    result = export_to_zotero.run_apply(limit=1, registry_path=registry_path)

    export_anchor = next(
        index
        for index, block in enumerate(notion_client.children)
        if block["type"] == "heading_1"
        and block["heading_1"]["rich_text"][0]["text"]["content"] == "EXPORT_AUDIT"
    )
    user_notes_anchor = next(
        index
        for index, block in enumerate(notion_client.children)
        if block["type"] == "heading_1"
        and block["heading_1"]["rich_text"][0]["text"]["content"] == "USER_NOTES"
    )
    audit_blocks = notion_client.children[export_anchor + 1 : user_notes_anchor]

    assert result["failed"] == 1
    assert notion_client.updates[-1][1]["properties"]["Zotero 状态"]["select"]["name"] == "导出失败"
    assert "export_failed" in audit_blocks[0]["paragraph"]["rich_text"][0]["text"]["content"]


def test_export_apply_persists_prior_registry_updates_when_later_claim_crashes(tmp_path, monkeypatch):
    registry_path = tmp_path / "state" / "registry.json"
    first_page = _page(page_id="page-1", paper_id="doi:10.1177/example-1", title="First Paper")
    second_page = _page(page_id="page-2", paper_id="doi:10.1177/example-2", title="Second Paper")

    class _NotionClient:
        def __init__(self, *args, **kwargs):
            self.updates = []

        def query_database(self, database_id, payload):
            if payload["filter"].get("and"):
                return {"results": []}
            return {"results": [first_page, second_page]}

        def update_page(self, page_id, payload):
            if page_id == "page-2" and payload["properties"]["Zotero 状态"]["select"]["name"] == "导出中":
                raise RuntimeError("claim failed")
            self.updates.append((page_id, payload))
            return {"id": page_id}

    class _ZoteroClient:
        def __init__(self, *args, **kwargs):
            self.counter = 0

        def validate_access(self):
            return True

        def create_item(self, payload):
            self.counter += 1
            return {"successful": {"0": {"key": f"ITEM{self.counter}"}}}

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
    monkeypatch.setattr(export_to_zotero, "NotionClient", lambda *args, **kwargs: _NotionClient())
    monkeypatch.setattr(export_to_zotero, "ZoteroClient", lambda *args, **kwargs: _ZoteroClient())
    monkeypatch.setattr(export_to_zotero, "_utc_now", lambda: "2026-03-30T10:20:00+00:00")

    try:
        export_to_zotero.run_apply(limit=2, registry_path=registry_path)
    except RuntimeError as error:
        assert "claim failed" in str(error)
    else:
        raise AssertionError("Expected second-page claim failure to abort the run")

    registry = load_registry(registry_path)
    assert registry["papers"]["doi:10.1177/example-1"]["export"]["zotero_item_key"] == "ITEM1"
    assert registry["papers"]["doi:10.1177/example-1"]["export"]["last_export_terminal_state"] == "已导出"
