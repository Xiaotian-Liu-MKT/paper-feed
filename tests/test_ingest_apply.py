from types import SimpleNamespace
import json

from paper_feed.notion_api import NotionApiError
from paper_feed.notion_body import MetadataChunkLimitError, resolve_creators_from_metadata_blocks
from paper_feed.notion_schema import PAGE_BODY_ANCHORS
from paper_feed.models import CanonicalPaperRecord
from scripts import ingest_to_notion


def _record():
    return CanonicalPaperRecord(
        paper_id="doi:10.1177/example",
        source_id="legacy-1",
        title="Example Paper",
        title_zh="示例论文",
        method="Experiment",
        topic="AI与营销技术",
        topics=[{"name": "AI与营销技术", "confidence": 0.9}],
        authors=[{"full_name": "Xiaotian Li", "family_name": "Li", "given_name": "Xiaotian"}],
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
    )


def test_apply_creates_page_when_lookup_is_empty(monkeypatch):
    record = _record()

    class _Client:
        def __init__(self, *args, **kwargs):
            self.created_payload = None

        def query_database(self, database_id, payload):
            return {"results": []}

        def create_page(self, payload):
            self.created_payload = payload
            return {"id": "page-1", "properties": {}}

        def update_page(self, page_id, payload):
            raise AssertionError("update_page should not be used in create path")

    monkeypatch.setattr(ingest_to_notion, "load_canonical_records", lambda: [record])
    monkeypatch.setattr(
        ingest_to_notion,
        "IntegrationSettings",
        lambda: SimpleNamespace(
            notion_token="token",
            notion_api_version="2022-06-28",
            notion_papers_database_id="db-1",
        ),
    )
    client = _Client()
    monkeypatch.setattr(ingest_to_notion, "NotionClient", lambda *args, **kwargs: client)
    monkeypatch.setattr(
        ingest_to_notion,
        "build_initial_page_body_blocks",
        lambda *args, **kwargs: [{"object": "block", "type": "heading_1", "heading_1": {"rich_text": [{"type": "text", "text": {"content": "SYSTEM_RAW_ABSTRACT"}}]}}],
    )
    monkeypatch.setattr(
        ingest_to_notion,
        "sync_page_body",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("sync_page_body should not run when children are in create payload")),
    )

    result = ingest_to_notion.run_apply(limit=10)

    assert result["created"] == 1
    assert result["updated"] == 0
    assert client.created_payload["parent"] == {"database_id": "db-1"}
    assert client.created_payload["children"][0]["heading_1"]["rich_text"][0]["text"]["content"] == "SYSTEM_RAW_ABSTRACT"


def test_apply_updates_page_when_lookup_finds_one(monkeypatch):
    record = _record()

    class _Client:
        def __init__(self, *args, **kwargs):
            self.updated = []

        def query_database(self, database_id, payload):
            return {
                "results": [
                    {
                        "id": "page-1",
                        "properties": {
                            "人工锁定": {"checkbox": False},
                            "Zotero 状态": {"select": {"name": "未导出"}},
                            "Ingested At": {"date": {"start": "2026-03-30T10:00:00+00:00"}},
                        },
                    }
                ]
            }

        def create_page(self, payload):
            raise AssertionError("create_page should not be used in update path")

        def update_page(self, page_id, payload):
            self.updated.append((page_id, payload))
            return {"id": page_id}

    monkeypatch.setattr(ingest_to_notion, "load_canonical_records", lambda: [record])
    monkeypatch.setattr(
        ingest_to_notion,
        "IntegrationSettings",
        lambda: SimpleNamespace(
            notion_token="token",
            notion_api_version="2022-06-28",
            notion_papers_database_id="db-1",
        ),
    )
    client = _Client()
    monkeypatch.setattr(ingest_to_notion, "NotionClient", lambda *args, **kwargs: client)

    result = ingest_to_notion.run_apply(limit=10)

    assert result["created"] == 0
    assert result["updated"] == 1
    assert client.updated[0][0] == "page-1"


def test_apply_rejects_duplicate_lookup_results(monkeypatch, tmp_path):
    record = _record()
    registry_path = tmp_path / "state" / "registry.json"

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def query_database(self, database_id, payload):
            return {"results": [{"id": "page-1"}, {"id": "page-2"}]}

    monkeypatch.setattr(ingest_to_notion, "load_canonical_records", lambda: [record])
    monkeypatch.setattr(
        ingest_to_notion,
        "IntegrationSettings",
        lambda: SimpleNamespace(
            notion_token="token",
            notion_api_version="2022-06-28",
            notion_papers_database_id="db-1",
        ),
    )
    monkeypatch.setattr(ingest_to_notion, "NotionClient", lambda *args, **kwargs: _Client())

    result = ingest_to_notion.run_apply(limit=10, registry_path=registry_path)
    report_paths = list((tmp_path / "state" / "duplicate_audit").glob("*.json"))
    payload = json.loads(report_paths[0].read_text(encoding="utf-8"))

    assert result["duplicates"] == 1
    assert result["processed"] == 1
    assert len(report_paths) == 1
    assert payload["entry_count"] == 1
    assert payload["entries"][0]["sample_page_ids"] == ["page-1", "page-2"]


def test_apply_uses_registry_page_id_without_exact_query(monkeypatch, tmp_path):
    registry_path = tmp_path / "state" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        '{"metadata":{"schema_version":1,"last_compaction_week":""},"papers":{"doi:10.1177/example":{"paper_id":"doi:10.1177/example","ingest":{"notion_page_id":"page-reg"}}}}',
        encoding="utf-8",
    )

    record = _record()

    class _Client:
        def __init__(self, *args, **kwargs):
            self.updated = []

        def query_database(self, database_id, payload):
            raise AssertionError("query_database should not run when registry already has notion_page_id")

        def retrieve_page(self, page_id):
            return {
                "id": page_id,
                "properties": {
                    "人工锁定": {"checkbox": True},
                    "Zotero 状态": {"select": {"name": "未导出"}},
                    "Ingested At": {"date": {"start": "2026-03-30T10:00:00+00:00"}},
                },
            }

        def create_page(self, payload):
            raise AssertionError("create_page should not run when registry already has notion_page_id")

        def update_page(self, page_id, payload):
            self.updated.append((page_id, payload))
            return {"id": page_id}

    monkeypatch.setattr(ingest_to_notion, "load_canonical_records", lambda: [record])
    monkeypatch.setattr(
        ingest_to_notion,
        "IntegrationSettings",
        lambda: SimpleNamespace(
            notion_token="token",
            notion_api_version="2022-06-28",
            notion_papers_database_id="db-1",
        ),
    )
    client = _Client()
    monkeypatch.setattr(ingest_to_notion, "NotionClient", lambda *args, **kwargs: client)

    result = ingest_to_notion.run_apply(registry_path=registry_path)

    assert result["updated"] == 1
    assert set(client.updated[0][1]["properties"].keys()) == {"Upstream Fingerprint", "Last Synced At"}


def test_apply_falls_back_to_exact_lookup_when_registry_page_id_is_stale(monkeypatch, tmp_path):
    registry_path = tmp_path / "state" / "registry.json"
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        '{"metadata":{"schema_version":1,"last_compaction_week":""},"papers":{"doi:10.1177/example":{"paper_id":"doi:10.1177/example","ingest":{"notion_page_id":"page-stale"}}}}',
        encoding="utf-8",
    )

    record = _record()

    class _Client:
        def __init__(self, *args, **kwargs):
            self.updated = []

        def retrieve_page(self, page_id):
            raise NotionApiError("missing", status_code=404)

        def query_database(self, database_id, payload):
            return {
                "results": [
                    {
                        "id": "page-1",
                        "properties": {
                            "人工锁定": {"checkbox": False},
                            "Zotero 状态": {"select": {"name": "未导出"}},
                            "Ingested At": {"date": {"start": "2026-03-30T10:00:00+00:00"}},
                        },
                    }
                ]
            }

        def update_page(self, page_id, payload):
            self.updated.append((page_id, payload))
            return {"id": page_id}

    monkeypatch.setattr(ingest_to_notion, "load_canonical_records", lambda: [record])
    monkeypatch.setattr(
        ingest_to_notion,
        "IntegrationSettings",
        lambda: SimpleNamespace(
            notion_token="token",
            notion_api_version="2022-06-28",
            notion_papers_database_id="db-1",
        ),
    )
    monkeypatch.setattr(ingest_to_notion, "NotionClient", lambda *args, **kwargs: _Client())

    result = ingest_to_notion.run_apply(registry_path=registry_path)

    assert result["updated"] == 1


def test_apply_enforces_exact_lookup_safeguard(monkeypatch, tmp_path):
    records = []
    for index in range(101):
        record = _record()
        record.paper_id = f"doi:10.1177/example-{index}"
        records.append(record)

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def query_database(self, database_id, payload):
            return {"results": []}

        def create_page(self, payload):
            return {"id": "page-created"}

        def update_page(self, page_id, payload):
            return {"id": page_id}

    monkeypatch.setattr(ingest_to_notion, "load_canonical_records", lambda: records)
    monkeypatch.setattr(
        ingest_to_notion,
        "IntegrationSettings",
        lambda: SimpleNamespace(
            notion_token="token",
            notion_api_version="2022-06-28",
            notion_papers_database_id="db-1",
        ),
    )
    monkeypatch.setattr(ingest_to_notion, "NotionClient", lambda *args, **kwargs: _Client())

    try:
        ingest_to_notion.run_apply(registry_path=tmp_path / "state" / "registry.json")
    except SystemExit as error:
        assert "safeguard" in str(error)
    else:
        raise AssertionError("Expected exact lookup safeguard to abort the run")

    report_paths = list((tmp_path / "state" / "duplicate_audit").glob("*.json"))
    payload = json.loads(report_paths[0].read_text(encoding="utf-8"))
    assert len(report_paths) == 1
    assert payload["degraded_reason"] == "exact_lookup_safeguard_exceeded"
    assert payload["entry_count"] == 0


def test_apply_respects_paper_id_filter(monkeypatch):
    first = _record()
    second = _record()
    second.paper_id = "doi:10.1177/other"
    second.title = "Other Paper"

    class _Client:
        def __init__(self, *args, **kwargs):
            self.created = 0

        def query_database(self, database_id, payload):
            return {"results": []}

        def create_page(self, payload):
            self.created += 1
            return {"id": f"page-{self.created}"}

        def update_page(self, page_id, payload):
            return {"id": page_id}

    monkeypatch.setattr(ingest_to_notion, "load_canonical_records", lambda: [first, second])
    monkeypatch.setattr(
        ingest_to_notion,
        "IntegrationSettings",
        lambda: SimpleNamespace(
            notion_token="token",
            notion_api_version="2022-06-28",
            notion_papers_database_id="db-1",
        ),
    )
    client = _Client()
    monkeypatch.setattr(ingest_to_notion, "NotionClient", lambda *args, **kwargs: client)

    result = ingest_to_notion.run_apply(paper_id_filter={"doi:10.1177/other"})

    assert result["processed"] == 1
    assert result["created"] == 1


def test_apply_respects_offset(monkeypatch):
    first = _record()
    second = _record()
    second.paper_id = "doi:10.1177/other"
    second.title = "Other Paper"

    class _Client:
        def __init__(self, *args, **kwargs):
            self.created_payloads = []

        def query_database(self, database_id, payload):
            return {"results": []}

        def create_page(self, payload):
            self.created_payloads.append(payload)
            return {"id": f"page-{len(self.created_payloads)}"}

        def update_page(self, page_id, payload):
            return {"id": page_id}

    monkeypatch.setattr(ingest_to_notion, "load_canonical_records", lambda: [first, second])
    monkeypatch.setattr(
        ingest_to_notion,
        "IntegrationSettings",
        lambda: SimpleNamespace(
            notion_token="token",
            notion_api_version="2022-06-28",
            notion_papers_database_id="db-1",
        ),
    )
    client = _Client()
    monkeypatch.setattr(ingest_to_notion, "NotionClient", lambda *args, **kwargs: client)

    result = ingest_to_notion.run_apply(limit=10, offset=1)

    assert result["processed"] == 1
    assert result["created"] == 1
    assert client.created_payloads[0]["properties"]["paper_id"]["rich_text"][0]["text"]["content"] == "doi:10.1177/other"


def test_apply_force_metadata_repair_requires_explicit_filter(monkeypatch):
    monkeypatch.setattr(
        ingest_to_notion,
        "IntegrationSettings",
        lambda: SimpleNamespace(
            notion_token="token",
            notion_api_version="2022-06-28",
            notion_papers_database_id="db-1",
        ),
    )

    try:
        ingest_to_notion.run_apply(force_metadata_repair=True)
    except SystemExit as error:
        assert "paper_id_filter" in str(error)
    else:
        raise AssertionError("Expected force metadata repair to require an explicit paper_id_filter")


def test_sync_page_body_bootstraps_anchors_and_owned_sections():
    record = _record()

    class _Client:
        def __init__(self):
            self.children = []
            self.deleted = []
            self._counter = 0

        def iter_block_children(self, block_id, *, page_size=100):
            return list(self.children)

        def append_block_children(self, block_id, children, after=None):
            inserted = []
            for child in children:
                self._counter += 1
                block = dict(child)
                block["id"] = f"block-{self._counter}"
                inserted.append(block)
            if after:
                index = next(i for i, block in enumerate(self.children) if block["id"] == after) + 1
            else:
                index = len(self.children)
            self.children[index:index] = inserted
            return {"results": inserted}

        def delete_block(self, block_id):
            self.deleted.append(block_id)
            self.children = [block for block in self.children if block["id"] != block_id]
            return {"id": block_id}

    client = _Client()

    ingest_to_notion.sync_page_body(
        client,
        "page-1",
        record,
        existing_state={"is_locked": False, "zotero_status": "未导出"},
    )

    headings = [
        block["heading_1"]["rich_text"][0]["text"]["content"]
        for block in client.children
        if block["type"] == "heading_1"
    ]
    assert headings == PAGE_BODY_ANCHORS

    metadata_anchor = next(
        index
        for index, block in enumerate(client.children)
        if block["type"] == "heading_1"
        and block["heading_1"]["rich_text"][0]["text"]["content"] == "SYSTEM_METADATA_JSON"
    )
    export_anchor = next(
        index
        for index, block in enumerate(client.children)
        if block["type"] == "heading_1"
        and block["heading_1"]["rich_text"][0]["text"]["content"] == "EXPORT_AUDIT"
    )
    metadata_blocks = client.children[metadata_anchor + 1 : export_anchor]

    assert resolve_creators_from_metadata_blocks(metadata_blocks)[0]["family_name"] == "Li"
    assert client.deleted == []


def test_sync_page_body_skips_metadata_rewrite_during_export_in_progress():
    record = _record()
    metadata_blocks = ingest_to_notion.build_metadata_blocks(record.authors)

    class _Client:
        def __init__(self):
            self.children = []
            self.deleted = []
            self.append_calls = []
            self._counter = 0
            for anchor in PAGE_BODY_ANCHORS:
                self._append_existing(
                    {
                        "object": "block",
                        "type": "heading_1",
                        "heading_1": {"rich_text": [{"type": "text", "text": {"content": anchor}}]},
                    }
                )
                if anchor == "SYSTEM_METADATA_JSON":
                    for block in metadata_blocks:
                        self._append_existing(block)

        def _append_existing(self, block):
            self._counter += 1
            existing = dict(block)
            existing["id"] = f"block-{self._counter}"
            self.children.append(existing)

        def iter_block_children(self, block_id, *, page_size=100):
            return list(self.children)

        def append_block_children(self, block_id, children, after=None):
            self.append_calls.append(after)
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
            self.deleted.append(block_id)
            self.children = [block for block in self.children if block["id"] != block_id]
            return {"id": block_id}

    client = _Client()

    ingest_to_notion.sync_page_body(
        client,
        "page-1",
        record,
        existing_state={"is_locked": False, "zotero_status": "导出中"},
    )

    metadata_anchor = next(
        index
        for index, block in enumerate(client.children)
        if block["type"] == "heading_1"
        and block["heading_1"]["rich_text"][0]["text"]["content"] == "SYSTEM_METADATA_JSON"
    )
    export_anchor = next(
        index
        for index, block in enumerate(client.children)
        if block["type"] == "heading_1"
        and block["heading_1"]["rich_text"][0]["text"]["content"] == "EXPORT_AUDIT"
    )
    metadata_section = client.children[metadata_anchor + 1 : export_anchor]

    assert resolve_creators_from_metadata_blocks(metadata_section)[0]["family_name"] == "Li"
    assert client.deleted == []


def test_sync_page_body_logs_authors_too_large_under_ingest_audit(monkeypatch):
    record = _record()

    class _Client:
        def __init__(self):
            self.children = []
            self.deleted = []
            self._counter = 0

        def iter_block_children(self, block_id, *, page_size=100):
            return list(self.children)

        def append_block_children(self, block_id, children, after=None):
            inserted = []
            for child in children:
                self._counter += 1
                block = dict(child)
                block["id"] = f"block-{self._counter}"
                inserted.append(block)
            if after:
                index = next(i for i, block in enumerate(self.children) if block["id"] == after) + 1
            else:
                index = len(self.children)
            self.children[index:index] = inserted
            return {"results": inserted}

        def delete_block(self, block_id):
            self.deleted.append(block_id)
            self.children = [block for block in self.children if block["id"] != block_id]
            return {"id": block_id}

    def _raise(*args, **kwargs):
        raise MetadataChunkLimitError("too big")

    monkeypatch.setattr(ingest_to_notion, "build_metadata_blocks", _raise)
    client = _Client()

    result = ingest_to_notion.sync_page_body(
        client,
        "page-1",
        record,
        existing_state={"is_locked": False, "zotero_status": "未导出"},
    )

    ingest_anchor = next(
        index
        for index, block in enumerate(client.children)
        if block["type"] == "heading_1"
        and block["heading_1"]["rich_text"][0]["text"]["content"] == "INGEST_AUDIT"
    )
    export_anchor = next(
        index
        for index, block in enumerate(client.children)
        if block["type"] == "heading_1"
        and block["heading_1"]["rich_text"][0]["text"]["content"] == "EXPORT_AUDIT"
    )
    audit_blocks = client.children[ingest_anchor + 1 : export_anchor]

    assert result["updated_sections"] == ["SYSTEM_RAW_ABSTRACT", "INGEST_AUDIT"]
    assert any(
        "authors_too_large" in block["paragraph"]["rich_text"][0]["text"]["content"]
        for block in audit_blocks
        if block["type"] == "paragraph"
    )
