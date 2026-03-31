import json

from paper_feed.models import CanonicalPaperRecord
from scripts import ingest_to_notion


def test_render_dry_run_defaults_statuses_and_preserves_identity():
    records = [
        CanonicalPaperRecord(
            paper_id="doi:10.1/example",
            source_id="legacy-1",
            title="Example Paper",
            title_zh="示例论文",
            method="Unclassified",
            topic="AI与营销技术",
            journal="Journal of Marketing",
            published_at="2026-03-30",
        )
    ]

    preview = ingest_to_notion.render_dry_run(records)

    assert preview == [
        {
            "paper_id": "doi:10.1/example",
            "title": "Example Paper",
            "status": "待看",
            "zotero_status": "未导出",
            "method": "Unclassified",
            "topic": "AI与营销技术",
        }
    ]


def test_main_dry_run_prints_preview_without_notion_client(monkeypatch, capsys):
    records = [
        CanonicalPaperRecord(
            paper_id="hash:abc",
            source_id="legacy-2",
            title="Dry Run Paper",
            title_zh="",
            method="Qualitative",
            topic="Other Marketing",
        )
    ]

    monkeypatch.setattr(ingest_to_notion, "load_canonical_records", lambda: records)

    class _ForbiddenClient:
        def __init__(self, *args, **kwargs):
            raise AssertionError("Notion client should not be constructed in dry-run mode")

    monkeypatch.setattr(ingest_to_notion, "NotionClient", _ForbiddenClient)

    exit_code = ingest_to_notion.main(["--dry-run"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)

    assert exit_code == 0
    assert payload["mode"] == "dry-run"
    assert payload["count"] == 1
    assert payload["items"][0]["paper_id"] == "hash:abc"
