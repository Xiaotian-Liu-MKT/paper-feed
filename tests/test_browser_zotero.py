import server


def _item(**overrides):
    payload = {
        "id": "source-1",
        "title": "Example Paper",
        "title_zh": "示例论文",
        "method": "Experiment",
        "topic": "AI与营销技术",
        "topics": [{"name": "AI与营销技术", "confidence": 0.9}],
        "link": "https://example.org/paper?utm_source=rss",
        "summary": "Publication date: 2026-03-31 Source: Journal of Marketing Author(s): Xiaotian Li, Ada Wong",
        "abstract": "Short abstract",
        "abstract_source": "crossref",
        "raw_abstract": "",
        "journal": "Journal of Marketing",
        "pub_date": "2026-03-31T00:00:00+00:00",
        "doi": "10.1177/example",
    }
    payload.update(overrides)
    return payload


def test_split_author_names_handles_comma_separated_names():
    creators = server.split_author_names("Xiaotian Li, Ada Wong")

    assert creators[0]["family_name"] == "Li"
    assert creators[0]["given_name"] == "Xiaotian"
    assert creators[1]["family_name"] == "Wong"
    assert creators[1]["given_name"] == "Ada"


def test_build_browser_export_payload_includes_translation_and_abstract():
    payload, paper_id = server.build_browser_export_payload(_item())

    assert paper_id.startswith("doi:")
    assert payload["DOI"] == "10.1177/example"
    assert payload["abstractNote"] == "Short abstract"
    assert "Chinese Title: 示例论文" in payload["extra"]
    assert any(tag["tag"] == f"pf:id:{paper_id}" for tag in payload["tags"])


def test_build_browser_export_payload_skips_gpt_generated_abstract_note():
    payload, _paper_id = server.build_browser_export_payload(
        _item(raw_abstract="", abstract="Predicted summary", abstract_source="gpt_generated")
    )

    assert "abstractNote" not in payload


def test_build_browser_export_payload_skips_speculative_ai_abstracts():
    payload, _ = server.build_browser_export_payload(
        _item(
            abstract="Predicted summary",
            raw_abstract="",
            abstract_source="gpt_generated",
        )
    )

    assert "abstractNote" not in payload


def test_export_favorites_to_zotero_skips_cached_and_reconciles_existing():
    class _ZoteroClient:
        def __init__(self):
            self.created = []

        def retrieve_item(self, item_key):
            if item_key == "ITEM-1":
                return {"key": "ITEM-1"}
            raise AssertionError(f"Unexpected retrieve_item({item_key})")

        def find_item_by_tag(self, tag):
            if tag.endswith("example-2"):
                return {"key": "ITEM-2"}
            return None

        def create_item(self, payload):
            self.created.append(payload)
            return {"successful": {"0": {"key": "ITEM-3"}}}

    items = [
        _item(link="https://example.org/one", doi="10.1177/example-1", title="First"),
        _item(link="https://example.org/two", doi="10.1177/example-2", title="Second"),
        _item(link="https://example.org/three", doi="10.1177/example-3", title="Third"),
    ]
    favorites = [
        "https://example.org/one",
        "https://example.org/two",
        "https://example.org/three",
        "https://example.org/missing",
    ]
    export_cache = {
        "https://example.org/one": {
            "item_key": "ITEM-1",
            "paper_id": "doi:10.1177/example-1",
        }
    }

    result = server.export_favorites_to_zotero(items, favorites, export_cache, _ZoteroClient())

    assert result["summary"]["requested"] == 4
    assert result["summary"]["skipped"] == 1
    assert result["summary"]["reconciled"] == 1
    assert result["summary"]["created"] == 1
    assert result["summary"]["missing"] == 1
    assert result["exports"]["https://example.org/two"]["item_key"] == "ITEM-2"
    assert result["exports"]["https://example.org/three"]["item_key"] == "ITEM-3"
