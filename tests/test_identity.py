from paper_feed.identity import build_paper_id


def test_build_paper_id_prefers_doi():
    record = {
        "doi": "10.1177/00222429241234567",
        "canonical_url": "https://example.org/paper",
        "title": "A Paper",
        "journal": "Journal of Marketing",
        "published_at": "2026-03-30",
    }

    assert build_paper_id(record) == "doi:10.1177/00222429241234567"


def test_build_paper_id_falls_back_to_url_hash():
    record = {
        "doi": "",
        "canonical_url": "https://example.org/paper?id=1&utm_source=rss",
        "title": "A Paper",
        "journal": "Journal of Marketing",
        "published_at": "2026-03-30",
    }

    result = build_paper_id(record)

    assert result.startswith("url:")
    assert len(result) == 68
