import datetime
import os
import tempfile
import unittest

import get_RSS


RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel><title>Test</title>
<item><title>First paper</title><link>https://example.test/1</link><guid>one</guid><pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate><description>marketing</description><author>Journal</author></item>
<item><title>Second paper</title><link>https://example.test/2</link><guid>two</guid><pubDate>Tue, 02 Jan 2024 00:00:00 +0000</pubDate><description>consumer behavior</description><author>Journal</author></item>
</channel></rss>"""


class HistoricalFeedTests(unittest.TestCase):
    def test_get_existing_items_preserves_every_item(self):
        with tempfile.TemporaryDirectory() as directory:
            original_output = get_RSS.OUTPUT_FILE
            try:
                get_RSS.OUTPUT_FILE = os.path.join(directory, "feed.xml")
                with open(get_RSS.OUTPUT_FILE, "w", encoding="utf-8") as handle:
                    handle.write(RSS_FIXTURE)

                items = get_RSS.get_existing_items()
            finally:
                get_RSS.OUTPUT_FILE = original_output

        self.assertEqual([item["id"] for item in items], ["one", "two"])
        self.assertTrue(all(isinstance(item["pub_date"], datetime.datetime) for item in items))

    def test_keyword_matching_requires_all_and_terms(self):
        entry = {"title": "Consumer behavior in social media", "summary": ""}
        self.assertTrue(get_RSS.match_entry(entry, ["social media AND consumer behavior"]))
        self.assertFalse(get_RSS.match_entry(entry, ["social media AND pricing"]))


if __name__ == "__main__":
    unittest.main()
