from pathlib import Path

import get_RSS


RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Test Feed</title>
    <item>
      <title>Paper One</title>
      <link>https://example.org/one</link>
      <guid>one</guid>
      <author>Journal One</author>
      <pubDate>Mon, 30 Mar 2026 00:00:00 GMT</pubDate>
      <description>Publication date: 2026-03-30 Source: Journal One</description>
    </item>
    <item>
      <title>Paper Two</title>
      <link>https://example.org/two</link>
      <guid>two</guid>
      <author>Journal Two</author>
      <pubDate>Tue, 31 Mar 2026 00:00:00 GMT</pubDate>
      <description>Publication date: 2026-03-31 Source: Journal Two</description>
    </item>
  </channel>
</rss>
"""


def test_get_existing_items_reads_all_entries(tmp_path, monkeypatch):
    xml_path = Path(tmp_path) / "feed.xml"
    xml_path.write_text(RSS_XML, encoding="utf-8")
    monkeypatch.setattr(get_RSS, "OUTPUT_FILE", str(xml_path))

    items = get_RSS.get_existing_items()

    assert [item["id"] for item in items] == ["one", "two"]
