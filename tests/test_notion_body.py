import json

import pytest

from paper_feed.notion_body import (
    MetadataChunkLimitError,
    append_audit_entry,
    build_initial_page_body_blocks,
    build_metadata_blocks,
    build_raw_abstract_blocks,
    ensure_page_body_anchors,
    resolve_creators_from_metadata_blocks,
)
from paper_feed.notion_schema import PAGE_BODY_ANCHORS


def test_build_raw_abstract_blocks_normalizes_source_and_prefix():
    blocks = build_raw_abstract_blocks("Line one\nLine two", "user")

    assert blocks[0]["type"] == "paragraph"
    assert blocks[0]["paragraph"]["rich_text"][0]["text"]["content"] == "source: unknown"
    assert blocks[1]["paragraph"]["rich_text"][0]["text"]["content"] == "Line one"
    assert blocks[2]["paragraph"]["rich_text"][0]["text"]["content"] == "Line two"


def test_build_metadata_blocks_inline_manifest_contains_creators():
    creators = [{"creatorType": "author", "firstName": "Xiaotian", "lastName": "Li"}]

    blocks = build_metadata_blocks(creators)
    manifest = json.loads(blocks[0]["code"]["rich_text"][0]["text"]["content"])

    assert manifest["schema_version"] == 1
    assert manifest["creators_storage"] == "inline"
    assert manifest["creators"] == creators


def test_build_metadata_blocks_chunk_manifest_when_inline_payload_is_too_large():
    creators = [
        {
            "creatorType": "author",
            "firstName": f"Given{index}",
            "lastName": ("Family" + str(index)) * 35,
        }
        for index in range(12)
    ]

    blocks = build_metadata_blocks(creators, inline_limit=200, chunk_size=200)
    manifest = json.loads(blocks[0]["code"]["rich_text"][0]["text"]["content"])

    assert manifest["creators_storage"] == "chunked"
    assert manifest["creators_chunks"] == len(blocks) - 1
    assert blocks[1]["code"]["rich_text"][0]["text"]["content"].startswith("CREATORS_CHUNK 1/")
    assert resolve_creators_from_metadata_blocks(blocks) == creators


def test_build_metadata_blocks_raises_when_chunk_limit_is_exceeded():
    creators = [{"creatorType": "author", "name": "X" * 400}] * 30

    with pytest.raises(MetadataChunkLimitError):
        build_metadata_blocks(creators, inline_limit=10, chunk_size=20, max_chunks=2)


def test_build_initial_page_body_blocks_includes_expected_anchors_and_content():
    creators = [{"creatorType": "author", "firstName": "Xiaotian", "lastName": "Li"}]

    blocks = build_initial_page_body_blocks(
        "Line one",
        "crossref",
        creators,
        occurred_at="2026-03-31T09:00:00Z",
    )

    headings = [block["heading_1"]["rich_text"][0]["text"]["content"] for block in blocks if block["type"] == "heading_1"]

    assert headings == PAGE_BODY_ANCHORS
    assert any(block.get("type") == "paragraph" for block in blocks)
    assert any(block.get("type") == "code" for block in blocks)


def test_ensure_page_body_anchors_reports_missing_and_duplicate_repairs():
    class _Client:
        def __init__(self):
            self.children = [
                {
                    "id": "h1",
                    "type": "heading_1",
                    "heading_1": {"rich_text": [{"text": {"content": "SYSTEM_RAW_ABSTRACT"}}]},
                },
                {
                    "id": "h2",
                    "type": "heading_1",
                    "heading_1": {"rich_text": [{"text": {"content": "SYSTEM_RAW_ABSTRACT"}}]},
                },
            ]
            self.counter = 2

        def iter_block_children(self, block_id, *, page_size=100):
            return list(self.children)

        def append_block_children(self, block_id, children, after=None):
            inserted = []
            for child in children:
                self.counter += 1
                block = dict(child)
                block["id"] = f"h{self.counter}"
                inserted.append(block)
            index = len(self.children) if after is None else next(
                i for i, block in enumerate(self.children) if block["id"] == after
            ) + 1
            self.children[index:index] = inserted
            return {"results": inserted}

    sections, repairs = ensure_page_body_anchors(_Client(), "page-1", return_repairs=True)

    assert "SYSTEM_METADATA_JSON" in sections
    assert any("missing_anchor_recreated" in repair for repair in repairs)
    assert any("duplicate_anchor_ignored" in repair for repair in repairs)


def test_append_audit_entry_logs_anchor_repairs_before_user_message():
    class _Client:
        def __init__(self):
            self.children = [
                {
                    "id": "h1",
                    "type": "heading_1",
                    "heading_1": {"rich_text": [{"text": {"content": "EXPORT_AUDIT"}}]},
                },
                {
                    "id": "h2",
                    "type": "heading_1",
                    "heading_1": {"rich_text": [{"text": {"content": "EXPORT_AUDIT"}}]},
                },
            ]
            self.counter = 2

        def iter_block_children(self, block_id, *, page_size=100):
            return list(self.children)

        def append_block_children(self, block_id, children, after=None):
            inserted = []
            for child in children:
                self.counter += 1
                block = dict(child)
                block["id"] = f"h{self.counter}"
                inserted.append(block)
            index = len(self.children) if after is None else next(
                i for i, block in enumerate(self.children) if block["id"] == after
            ) + 1
            self.children[index:index] = inserted
            return {"results": inserted}

        def delete_block(self, block_id):
            self.children = [block for block in self.children if block["id"] != block_id]
            return {"id": block_id}

    client = _Client()
    append_audit_entry(client, "page-1", "EXPORT_AUDIT", "export_failed: boom", occurred_at="2026-03-30T10:20:00Z")
    paragraphs = [block["paragraph"]["rich_text"][0]["text"]["content"] for block in client.children if block["type"] == "paragraph"]

    assert any("duplicate_anchor_ignored" in paragraph for paragraph in paragraphs)
    assert "export_failed: boom" in paragraphs[-1]
