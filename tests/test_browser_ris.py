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
        "raw_abstract": "",
        "abstract_source": "crossref",
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


def test_build_browser_export_metadata_keeps_trusted_abstract_only():
    metadata = server.build_browser_export_metadata(_item())

    assert metadata["paper_id"].startswith("doi:")
    assert metadata["abstract"] == "Short abstract"
    assert metadata["source_host"] == "example.org"
    assert metadata["topics"] == ["AI与营销技术"]


def test_build_browser_export_metadata_skips_gpt_generated_abstract():
    metadata = server.build_browser_export_metadata(
        _item(raw_abstract="", abstract="Predicted summary", abstract_source="gpt_generated")
    )

    assert metadata["abstract"] == ""


def test_build_browser_ris_entry_contains_core_fields():
    ris = server.build_browser_ris_entry(_item())

    assert "TY  - JOUR" in ris
    assert "TI  - Example Paper" in ris
    assert "JO  - Journal of Marketing" in ris
    assert "AU  - Li, Xiaotian" in ris
    assert "AU  - Wong, Ada" in ris
    assert "DO  - 10.1177/example" in ris
    assert "UR  - https://example.org/paper?utm_source=rss" in ris
    assert "AB  - Short abstract" in ris
    assert "KW  - Experiment" in ris
    assert "KW  - AI与营销技术" in ris
    assert "N1  - Chinese Title: 示例论文" in ris
    assert "ER  - " in ris


def test_build_favorites_ris_exports_unique_links_and_reports_missing():
    items = [
        _item(link="https://example.org/one", doi="10.1177/example-1", title="First"),
        _item(link="https://example.org/two", doi="10.1177/example-2", title="Second"),
    ]
    favorites = [
        "https://example.org/one",
        "https://example.org/two",
        "https://example.org/one",
        "https://example.org/missing",
    ]

    result = server.build_favorites_ris(items, favorites)

    assert result["summary"]["requested"] == 3
    assert result["summary"]["exported"] == 2
    assert result["summary"]["missing"] == 1
    assert result["missing_links"] == ["https://example.org/missing"]
    assert result["ris"].count("TY  - JOUR") == 2
