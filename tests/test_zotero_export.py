from paper_feed.zotero_api import build_item_payload


def test_build_item_payload_uses_journal_article_and_machine_tag():
    payload = build_item_payload(
        title="Example",
        creators=[{"full_name": "Xiaotian Li", "family_name": "Li", "given_name": "Xiaotian"}],
        publication_title="Journal of Marketing",
        published_at="2026-03-30",
        url="https://example.org/paper",
        doi="10.1177/00222429241234567",
        paper_id="doi:10.1177/00222429241234567",
        notion_page_id="page-123",
        source="journals.sagepub.com",
    )

    assert payload["itemType"] == "journalArticle"
    assert payload["date"] == "2026-03-30"
    assert payload["DOI"] == "10.1177/00222429241234567"
    assert payload["creators"] == [{"creatorType": "author", "lastName": "Li", "firstName": "Xiaotian"}]
    assert {"tag": "pf:id:doi:10.1177/00222429241234567"} in payload["tags"]
    assert "Paper Feed ID: doi:10.1177/00222429241234567" in payload["extra"]
    assert "Notion Page ID: page-123" in payload["extra"]
