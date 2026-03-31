import json
from types import SimpleNamespace

from paper_feed.notion_api import NotionApiError
from scripts import bootstrap_notion_papers


def test_apply_requires_explicit_create_flag_when_database_id_missing(monkeypatch):
    monkeypatch.setattr(
        bootstrap_notion_papers,
        "IntegrationSettings",
        lambda: SimpleNamespace(
            notion_parent_page_id="parent-page",
            notion_papers_database_id="",
            notion_token="token",
            notion_api_version="2022-06-28",
        ),
    )

    try:
        bootstrap_notion_papers.main(["--apply"])
    except SystemExit as error:
        assert "--create-if-missing" in str(error)
    else:
        raise AssertionError("Expected apply mode to refuse implicit create mode")


def test_apply_returns_structured_probe_error(monkeypatch, capsys):
    monkeypatch.setattr(
        bootstrap_notion_papers,
        "IntegrationSettings",
        lambda: SimpleNamespace(
            notion_parent_page_id="parent-page",
            notion_papers_database_id="db-1",
            notion_token="token",
            notion_api_version="2022-06-28",
        ),
    )

    class _FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        def retrieve_page(self, page_id):
            raise NotionApiError(
                "Notion API request failed: GET /pages/parent-page",
                status_code=401,
                response_body='{"code":"unauthorized","message":"API token is invalid."}',
                code="unauthorized",
                notion_message="API token is invalid.",
            )

    monkeypatch.setattr(bootstrap_notion_papers, "NotionClient", _FailingClient)

    exit_code = bootstrap_notion_papers.main(["--apply"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 2
    assert payload["error"]["type"] == "authentication_failed"
    assert payload["error"]["stage"] == "read_parent"


def test_apply_reuses_existing_child_database_before_creating(monkeypatch, capsys):
    monkeypatch.setattr(
        bootstrap_notion_papers,
        "IntegrationSettings",
        lambda: SimpleNamespace(
            notion_parent_page_id="parent-page",
            notion_papers_database_id="",
            notion_token="token",
            notion_api_version="2022-06-28",
        ),
    )

    class _Client:
        def __init__(self, *args, **kwargs):
            self.created = False

        def retrieve_page(self, page_id):
            return {"id": page_id}

        def create_page(self, payload):
            return {"id": "scratch-page"}

        def archive_page(self, page_id):
            return {"id": page_id}

        def iter_block_children(self, block_id):
            yield {
                "id": "existing-db",
                "type": "child_database",
                "child_database": {"title": "Papers"},
            }

        def retrieve_database(self, database_id):
            return bootstrap_notion_papers.build_database_create_payload("parent-page")

        def update_database(self, database_id, payload):
            return {"id": database_id}

        def create_database(self, payload):
            self.created = True
            raise AssertionError("create_database should not be called when Papers already exists")

    monkeypatch.setattr(bootstrap_notion_papers, "NotionClient", _Client)

    exit_code = bootstrap_notion_papers.main(["--apply", "--create-if-missing"])
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["action"] == "reused_existing"
    assert payload["database_id"] == "existing-db"


def test_apply_fails_on_ambiguous_child_database_matches(monkeypatch):
    monkeypatch.setattr(
        bootstrap_notion_papers,
        "IntegrationSettings",
        lambda: SimpleNamespace(
            notion_parent_page_id="parent-page",
            notion_papers_database_id="",
            notion_token="token",
            notion_api_version="2022-06-28",
        ),
    )

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        def retrieve_page(self, page_id):
            return {"id": page_id}

        def create_page(self, payload):
            return {"id": "scratch-page"}

        def archive_page(self, page_id):
            return {"id": page_id}

        def iter_block_children(self, block_id):
            yield {
                "id": "existing-db-1",
                "type": "child_database",
                "child_database": {"title": "Papers"},
            }
            yield {
                "id": "existing-db-2",
                "type": "child_database",
                "child_database": {"title": "Papers"},
            }

    monkeypatch.setattr(bootstrap_notion_papers, "NotionClient", _Client)

    try:
        bootstrap_notion_papers.main(["--apply", "--create-if-missing"])
    except SystemExit as error:
        assert "NOTION_PAPERS_DATABASE_ID" in str(error)
    else:
        raise AssertionError("Expected bootstrap apply to fail on ambiguous child databases")
