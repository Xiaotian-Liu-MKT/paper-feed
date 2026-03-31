from datetime import datetime

from paper_feed.canonical import build_canonical_record


def test_build_canonical_record_keeps_normalized_fields():
    item = {
        "id": "https://example.org/item-1",
        "title": "[ScienceDirect Publication] Example Title",
        "link": "https://example.org/item-1?utm_source=rss",
        "summary": "<p>Publication date: 2026-03-30</p><p>Source: Journal of Marketing</p>",
        "journal": "ScienceDirect Publication: Journal of Marketing",
        "pub_date": datetime(2026, 3, 30),
    }
    analysis = {
        "zh": "示例标题",
        "methods": [{"name": "Experiment", "confidence": 0.9}],
        "topics": [{"name": "AI与营销技术", "confidence": 0.8}],
        "classification_version": "v2",
    }

    record = build_canonical_record(item, analysis, abstract_info={})

    assert record.source_id == item["id"]
    assert record.title == "Example Title"
    assert record.title_zh == "示例标题"
    assert record.method == "Experiment"
    assert record.topic == "AI与营销技术"
    assert record.journal == "Journal of Marketing"
    assert record.link == item["link"]
    assert record.published_at == "2026-03-30T00:00:00"
    assert record.paper_id.startswith("url:")
