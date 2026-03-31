from paper_feed.registry import (
    load_registry,
    merge_registry_entry,
    update_registry_namespace,
)


def test_merge_registry_entry_preserves_foreign_namespace():
    remote = {
        "paper_id": "hash:abc",
        "ingest": {"notion_page_id": "page-1"},
        "export": {"zotero_item_key": "item-1"},
    }
    local = {
        "paper_id": "hash:abc",
        "ingest": {"notion_page_id": "page-2"},
    }

    merged = merge_registry_entry(remote, local, owned_namespace="ingest")

    assert merged["ingest"]["notion_page_id"] == "page-2"
    assert merged["export"]["zotero_item_key"] == "item-1"


def test_load_registry_bootstraps_empty_state(tmp_path):
    registry_path = tmp_path / "state" / "registry.json"

    registry = load_registry(registry_path)

    assert registry["metadata"] == {"schema_version": 1, "last_compaction_week": ""}
    assert registry["papers"] == {}


def test_update_registry_namespace_creates_and_preserves_namespaces():
    registry = load_registry(None)

    update_registry_namespace(
        registry,
        "doi:10.1/example",
        "ingest",
        {"notion_page_id": "page-1"},
    )
    update_registry_namespace(
        registry,
        "doi:10.1/example",
        "export",
        {"zotero_item_key": "item-1"},
    )

    assert registry["papers"]["doi:10.1/example"]["ingest"]["notion_page_id"] == "page-1"
    assert registry["papers"]["doi:10.1/example"]["export"]["zotero_item_key"] == "item-1"
