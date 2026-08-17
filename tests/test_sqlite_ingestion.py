import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from xml.etree import ElementTree as ET
from unittest.mock import patch

import get_RSS
from paper_feed.db import PaperRepository, connect
from paper_feed.exporter import database_items, export_items
from paper_feed.ingestion import ingest_fetch_results


def entry(number, doi=None, guid=None):
    return {"id": guid or f"guid-{number}", "title": f"Marketing {number}", "link": doi or f"https://example.test/{number}",
            "journal": "Journal", "summary": "marketing", "pub_date": datetime(2024, 1, 1, tzinfo=timezone.utc)}


class SQLiteIngestionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.db = os.path.join(self.temp.name, "data", "feed.sqlite3")

    def tearDown(self): self.temp.cleanup()

    def ingest(self, results): return ingest_fetch_results(results, self.temp.name, self.db)

    def test_total_failure_preserves_exports_but_records_run(self):
        xml, feed = os.path.join(self.temp.name, "feed.xml"), os.path.join(self.temp.name, "feed.json")
        with open(xml, "wb") as handle: handle.write(b"old")
        with open(feed, "wb") as handle: handle.write(b"old-json")
        before = (hashlib.sha256(Path(xml).read_bytes()).hexdigest(), hashlib.sha256(Path(feed).read_bytes()).hexdigest())
        result = self.ingest([{"url": "bad", "success": False, "entries": [], "error": "offline"}])
        conn = connect(self.db)
        self.assertEqual(result["status"], "failed"); self.assertEqual(conn.execute("select status from fetch_runs order by started_at desc limit 1").fetchone()[0], "failed")
        self.assertEqual(conn.execute("select count(*) from papers").fetchone()[0], 0); conn.close()
        self.assertEqual(before, (hashlib.sha256(Path(xml).read_bytes()).hexdigest(), hashlib.sha256(Path(feed).read_bytes()).hexdigest()))

    def test_partial_idempotency_and_doi_guid_change(self):
        good = {"url": "one", "success": True, "entries": [entry(1, "https://doi.org/10.1000/demo", "old-guid")]}
        bad = {"url": "two", "success": False, "entries": []}
        self.assertEqual(self.ingest([good, bad])["status"], "partial_failed")
        good["entries"] = [entry(1, "doi:10.1000/demo", "new-guid")]
        self.assertEqual(self.ingest([good])["status"], "succeeded")
        conn = connect(self.db)
        self.assertEqual(conn.execute("select count(*) from papers").fetchone()[0], 1)
        self.assertEqual(conn.execute("select count(*) from paper_observations").fetchone()[0], 2)
        self.assertEqual(conn.execute("select count(*) from paper_review_state").fetchone()[0], 1); conn.close()

    def test_keyword_predicate_excludes_nonmatching_papers(self):
        matching, ignored = entry(1), entry(2)
        ignored["title"] = "Unrelated accounting paper"; ignored["summary"] = ""
        self.ingest([{"url": "one", "success": True, "entries": [matching, ignored]}],)
        # Re-run into a fresh database with the actual predicate (source counts retain both).
        other = os.path.join(self.temp.name, "second.sqlite3")
        result = ingest_fetch_results([{"url": "one", "success": True, "entries": [matching, ignored]}], self.temp.name, other,
                                      predicate=lambda item: "marketing" in (item["title"] + item["summary"]).lower())
        conn = connect(other)
        self.assertEqual(conn.execute("select count(*) from papers").fetchone()[0], 1)
        detail = json.loads(conn.execute("select detail_json from source_fetches order by source_fetch_id desc limit 1").fetchone()[0])
        self.assertEqual((detail["fetched_count"], detail["matched_count"]), (2, 1)); conn.close()

    def test_translation_analysis_persists_and_is_not_repeated(self):
        self.ingest([{"url": "one", "success": True, "entries": [entry(1)]}])
        items = database_items(self.db)
        config = {"OPENAI_API_KEY": "test"}
        result = {items[0]["title"]: {"zh": "中文", "methods": ["Experiment"], "topics": ["Other Marketing"], "classification_version": get_RSS.CLASSIFICATION_VERSION}}
        with patch.object(get_RSS, "batch_analyze_papers", return_value=result) as analyze:
            self.assertEqual(get_RSS.analyze_database_items(self.db, items, config), 1)
            self.assertEqual(get_RSS.analyze_database_items(self.db, database_items(self.db), config), 0)
        analyze.assert_called_once()
        self.assertEqual(database_items(self.db)[0]["translation"]["zh"], "中文")

    def test_reanalysis_exports_full_history_after_keywords_change(self):
        self.ingest([{"url": "one", "success": True, "entries": [entry(1)]}])
        xml, feed = os.path.join(self.temp.name, "out.xml"), os.path.join(self.temp.name, "web", "feed.json")
        with patch.dict(os.environ, {"PAPER_FEED_DB": self.db}), \
             patch.object(get_RSS, "get_config", return_value={"OPENAI_API_KEY": "test"}), \
             patch.object(get_RSS, "load_config", return_value=["unrelated accounting keyword"]), \
             patch.object(get_RSS, "batch_analyze_papers", return_value={}), \
             patch.object(get_RSS, "OUTPUT_FILE", xml), patch.object(get_RSS, "FEED_JSON", feed):
            result = get_RSS.run_reanalysis_flow()

        self.assertEqual(result["status"], "ok")
        with open(feed, encoding="utf-8") as handle:
            self.assertEqual([item["id"] for item in json.load(handle)["items"]], ["guid-1"])

    def test_transaction_rolls_back_conflicting_identities(self):
        self.ingest([{"url": "a", "success": True, "entries": [entry(1, "https://doi.org/10.1000/a"), entry(2, "https://doi.org/10.1000/b")]}])
        collision = entry(3, "https://doi.org/10.1000/a", "shared")
        collision["link"] = "https://doi.org/10.1000/b"
        collision["doi"] = "10.1000/a"
        with self.assertRaises(ValueError): self.ingest([{"url": "b", "success": True, "entries": [collision]}])
        conn = connect(self.db); self.assertEqual(conn.execute("select count(*) from paper_observations").fetchone()[0], 2); conn.close()

    def test_export_limits_only_projection_and_preserves_ids(self):
        records = [entry(i) for i in range(1001)]
        for i, record in enumerate(records): record["pub_date"] += timedelta(days=i)
        self.ingest([{"url": "one", "success": True, "entries": records}])
        items = database_items(self.db)
        self.assertEqual(len(items), 1001)
        xml, feed = os.path.join(self.temp.name, "out.xml"), os.path.join(self.temp.name, "web", "feed.json")
        payload = export_items(items, xml, feed, ["marketing"], limit=1000)
        self.assertEqual(len(payload["items"]), 1000); self.assertTrue(all(item["paper_id"] and item["id"] and item["link"] for item in payload["items"]))
        self.assertEqual(payload["items"][0]["id"], "guid-1000")
        self.assertNotIn("guid-0", [item["id"] for item in payload["items"]])
        self.assertEqual(len(ET.parse(xml).findall("./channel/item")), 1000)
        self.assertIsNotNone(parsedate_to_datetime(ET.parse(xml).findtext("./channel/item/pubDate")))
        with open(feed, encoding="utf-8") as handle: saved = json.load(handle)
        self.assertEqual(saved["items"][0]["paper_id"], payload["items"][0]["paper_id"])

    def test_summary_uses_full_database_and_durable_aliases(self):
        records = [entry(i) for i in range(1001)]
        for i, record in enumerate(records): record["pub_date"] += timedelta(days=i)
        self.ingest([{"url": "one", "success": True, "entries": records}])
        oldest = next(item for item in database_items(self.db) if item["id"] == "guid-0")
        conn = connect(self.db); repo = PaperRepository(conn)
        with repo.transaction(): repo.add_legacy_alias(oldest["paper_id"], "legacy-oldest")
        conn.close()
        xml, feed = os.path.join(self.temp.name, "out.xml"), os.path.join(self.temp.name, "web", "feed.json")
        with patch.dict(os.environ, {"PAPER_FEED_DB": self.db}), \
             patch.object(get_RSS, "get_config", return_value={"OPENAI_API_KEY": "test"}), \
             patch.object(get_RSS, "load_config", return_value=["unrelated accounting keyword"]), \
             patch.object(get_RSS, "OUTPUT_FILE", xml), patch.object(get_RSS, "FEED_JSON", feed), \
             patch.object(get_RSS, "generate_abstract_with_gpt", return_value="durable summary") as generate:
            result = get_RSS.summarize_specific_papers(["guid-0", oldest["paper_id"], "legacy-oldest"])
            again = get_RSS.summarize_specific_papers([oldest["paper_id"]])
        self.assertEqual((result["updated"], again["updated"]), (1, 0)); generate.assert_called_once()
        saved_oldest = next(item for item in database_items(self.db) if item["paper_id"] == oldest["paper_id"])
        self.assertEqual(saved_oldest["abstract"]["abstract"], "durable summary")
        with open(feed, encoding="utf-8") as handle: payload = json.load(handle)
        self.assertEqual(len(payload["items"]), 1000)
        self.assertTrue(all(item["paper_id"] for item in payload["items"]))


if __name__ == "__main__": unittest.main()
