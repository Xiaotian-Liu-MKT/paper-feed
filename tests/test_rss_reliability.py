import datetime
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import get_RSS
from paper_feed.db import connect
from paper_feed.ingestion import ensure_database


RSS_XML = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>Test Journal</title>
<item><title>Marketing paper</title><link>https://example.test/paper</link><guid>paper-1</guid>
<pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate><description>marketing</description></item>
</channel></rss>"""


class FetchRetryTests(unittest.TestCase):
    def test_404_is_not_retried(self):
        response = SimpleNamespace(status_code=404, content=b"")
        with patch.object(get_RSS.requests, "get", return_value=response) as request, patch.object(get_RSS.time, "sleep"):
            result = get_RSS.fetch_rss_result("https://example.test/missing", retries=3)

        self.assertFalse(result["success"])
        self.assertEqual(result["status_code"], 404)
        self.assertEqual(request.call_count, 1)

    def test_transient_429_and_5xx_are_retried(self):
        for status in (429, 503):
            with self.subTest(status=status):
                responses = [
                    SimpleNamespace(status_code=status, content=b""),
                    SimpleNamespace(status_code=200, content=RSS_XML),
                ]
                with patch.object(get_RSS.requests, "get", side_effect=responses) as request, patch.object(get_RSS.time, "sleep"):
                    result = get_RSS.fetch_rss_result("https://example.test/feed", retries=3)

                self.assertTrue(result["success"])
                self.assertEqual(result["attempts"], 2)
                self.assertEqual(request.call_count, 2)


class PublicationReliabilityTests(unittest.TestCase):
    def _entry(self):
        return {
            "id": "new-paper", "title": "Marketing paper", "summary": "marketing",
            "link": "https://example.test/new", "journal": "Test",
            "pub_date": datetime.datetime(2024, 1, 1),
        }

    def test_partial_success_publishes_available_entries(self):
        results = [
            {"url": "https://one.test/rss", "success": True, "entries": [self._entry()]},
            {"url": "https://two.test/rss", "success": False, "entries": []},
        ]
        with tempfile.TemporaryDirectory() as directory, \
            patch.object(get_RSS, "WEB_DIR", directory), \
            patch.object(get_RSS, "JOURNAL_HASH_FILE", os.path.join(directory, "journals.hash")), \
            patch.dict(os.environ, {"PAPER_FEED_DB": os.path.join(directory, "paper_feed.sqlite3")}), \
            patch.object(get_RSS, "load_config", side_effect=[list(x["url"] for x in results), ["marketing"]]), \
            patch.object(get_RSS, "fetch_rss_result", side_effect=results), \
            patch.object(get_RSS, "get_existing_items", return_value=[]), \
            patch.object(get_RSS, "get_config", return_value={}), \
            patch.object(get_RSS, "generate_rss_xml") as publish:
            # A created temporary schema prevents legacy bootstrap from reading
            # project XML and proves this test has no project database state.
            connect(os.path.join(directory, "paper_feed.sqlite3")).close()
            outcome = get_RSS.run_rss_flow()

        self.assertTrue(outcome["published"])
        self.assertEqual(outcome["successful_sources"], ["https://one.test/rss"])
        self.assertEqual(outcome["failed_sources"], ["https://two.test/rss"])
        self.assertEqual(outcome["new_items"], 1)
        publish.assert_called_once()
        self.assertEqual(publish.call_args.args[0][0]["id"], "new-paper")

    def test_clean_bootstrap_preserves_history_when_keywords_change(self):
        """CI must export accepted legacy history even if today's filter matches nothing."""
        history = b"""<?xml version=\"1.0\"?><rss version=\"2.0\"><channel><title>History</title>
        <item><title>Historical consumer study</title><link>https://example.test/history</link><guid>history-1</guid>
        <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate><description>consumer behavior</description><author>History Journal</author></item>
        </channel></rss>"""
        nonmatching = self._entry()
        nonmatching.update({"id": "ignored-new", "title": "Unrelated accounting paper", "summary": "earnings"})
        results = [{"url": "https://one.test/rss", "success": True, "entries": [nonmatching]}]
        with tempfile.TemporaryDirectory() as directory:
            output_xml = os.path.join(directory, "filtered_feed.xml")
            output_json = os.path.join(directory, "web", "feed.json")
            database = os.path.join(directory, "data", "paper_feed.sqlite3")
            with open(output_xml, "wb") as handle:
                handle.write(history)
            with patch.object(get_RSS, "WEB_DIR", os.path.join(directory, "web")), \
                patch.object(get_RSS, "OUTPUT_FILE", output_xml), \
                patch.object(get_RSS, "FEED_JSON", output_json), \
                patch.object(get_RSS, "JOURNAL_HASH_FILE", os.path.join(directory, "web", "journals.hash")), \
                patch.object(get_RSS, "ensure_database", side_effect=lambda _root, db: ensure_database(directory, db)), \
                patch.dict(os.environ, {"PAPER_FEED_DB": database}), \
                patch.object(get_RSS, "load_config", side_effect=[[results[0]["url"]], ["brand new keyword"]]), \
                patch.object(get_RSS, "fetch_rss_result", side_effect=results), \
                patch.object(get_RSS, "get_config", return_value={}):
                outcome = get_RSS.run_rss_flow()

            self.assertTrue(outcome["published"])
            self.assertEqual(outcome["new_items"], 0)
            with open(output_json, encoding="utf-8") as handle:
                exported = json.load(handle)
            self.assertEqual([item["id"] for item in exported["items"]], ["history-1"])
            conn = connect(database)
            self.assertEqual(conn.execute("SELECT count(*) FROM papers").fetchone()[0], 1)
            conn.close()

    def test_total_failure_does_not_overwrite_published_outputs(self):
        urls = ["https://one.test/rss", "https://two.test/rss"]
        results = [{"url": url, "success": False, "entries": []} for url in urls]
        with tempfile.TemporaryDirectory() as directory:
            hash_file = os.path.join(directory, "journals.hash")
            output_xml = os.path.join(directory, "filtered_feed.xml")
            output_json = os.path.join(directory, "web", "feed.json")
            with open(hash_file, "w", encoding="utf-8") as handle:
                handle.write("existing-hash")
            os.makedirs(os.path.dirname(output_json))
            with open(output_xml, "wb") as handle:
                handle.write(b"existing feed")
            with open(output_json, "wb") as handle:
                handle.write(b"existing json")
            connect(os.path.join(directory, "paper_feed.sqlite3")).close()
            with patch.object(get_RSS, "WEB_DIR", directory), \
                patch.object(get_RSS, "OUTPUT_FILE", output_xml), \
                patch.object(get_RSS, "FEED_JSON", output_json), \
                patch.object(get_RSS, "JOURNAL_HASH_FILE", hash_file), \
                patch.dict(os.environ, {"PAPER_FEED_DB": os.path.join(directory, "paper_feed.sqlite3")}), \
                patch.object(get_RSS, "load_config", side_effect=[urls, ["marketing"]]), \
                 patch.object(get_RSS, "fetch_rss_result", side_effect=results), \
                 patch.object(get_RSS, "generate_rss_xml") as publish:
                outcome = get_RSS.run_rss_flow()

            self.assertFalse(outcome["published"])
            self.assertEqual(outcome["failed_sources"], urls)
            self.assertEqual(outcome["new_items"], 0)
            publish.assert_not_called()
            with open(hash_file, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), "existing-hash")
            with open(output_xml, "rb") as handle:
                self.assertEqual(handle.read(), b"existing feed")
            with open(output_json, "rb") as handle:
                self.assertEqual(handle.read(), b"existing json")


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_fsyncs_then_replaces_destination(self):
        with tempfile.TemporaryDirectory() as directory:
            destination = os.path.join(directory, "output.json")
            with patch.object(get_RSS.os, "fsync", wraps=get_RSS.os.fsync) as fsync, \
                 patch.object(get_RSS.os, "replace", wraps=get_RSS.os.replace) as replace:
                get_RSS.atomic_write(destination, '{"ok": true}')

            self.assertTrue(fsync.called)
            replace.assert_called_once()
            self.assertEqual(replace.call_args.args[1], destination)
            with open(destination, encoding="utf-8") as handle:
                self.assertEqual(handle.read(), '{"ok": true}')
            self.assertEqual([name for name in os.listdir(directory) if name.startswith(".tmp-")], [])


if __name__ == "__main__":
    unittest.main()
