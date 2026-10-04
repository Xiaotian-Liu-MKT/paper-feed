"""CI bootstrap: a clean checkout rebuilds SQLite from the committed exports.

GitHub Actions deletes its SQLite cache every run, so everything that must survive
between runs (stable paper_id, title analyses) has to round-trip through
filtered_feed.xml + web/feed.json.  No network or OpenAI calls are made.
"""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

import get_RSS
from paper_feed.exporter import DEFAULT_CHANNEL_LINK, database_items, export_items
from paper_feed.importer import LegacyImporter, feed_translation_payload


def analysed(paper_id, number, translation=True):
    item = {
        "paper_id": paper_id, "id": f"https://example.test/{number}", "link": f"https://example.test/{number}",
        "title": f"Paper number {number}", "journal": "Journal of Tests",
        "pub_date": f"2024-01-{number:02d}T00:00:00+00:00", "summary": "Publication date: 2024",
    }
    if translation:
        item["translation"] = {
            "zh": f"论文 {number}",
            "methods": [{"name": "Experiment", "confidence": 0.9}],
            "topics": [{"name": "AI & Tech", "confidence": 0.8}],
            "theories": ["Construal level"], "context": ["Retail"], "subjects": ["Consumers"],
            "novelty_score": 4, "classification_version": get_RSS.CLASSIFICATION_VERSION,
        }
    return item


def clean_env(**extra):
    env = {key: value for key, value in os.environ.items()
           if key not in ("RSS_KEYWORDS", "GITHUB_REPOSITORY", "GITHUB_SERVER_URL")}
    env.update(extra)
    return patch.dict(os.environ, env, clear=True)


class CiBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "web").mkdir()
        self.xml = self.root / "filtered_feed.xml"
        self.json = self.root / "web" / "feed.json"

    def tearDown(self):
        self.temp.cleanup()

    def export(self, items, queries=("consumer AND trust",)):
        return export_items(items, self.xml, self.json, queries)

    def bootstrap(self, name="ci.sqlite3"):
        database = str(self.root / "data" / name)
        LegacyImporter(self.root, database).run(_backup_enabled=False)
        return database

    def test_bootstrap_restores_title_analyses_so_nothing_is_reanalysed(self):
        with clean_env():
            self.export([analysed("pid-1", 1), analysed("pid-2", 2), analysed("pid-3", 3, translation=False)])
        feed = json.loads(self.json.read_text(encoding="utf-8"))
        # The exporter does publish the analysis fields (CI simply never had any).
        first = next(item for item in feed["items"] if item["paper_id"] == "pid-1")
        self.assertEqual(first["title_zh"], "论文 1")
        self.assertEqual(first["method"], "Experiment")
        self.assertEqual(first["classification_version"], get_RSS.CLASSIFICATION_VERSION)

        items = database_items(self.bootstrap())
        by_id = {item["paper_id"]: item for item in items}
        # paper_id survives the clean bootstrap instead of being re-minted.
        self.assertEqual(set(by_id), {"pid-1", "pid-2", "pid-3"})
        stale = get_RSS.stale_analysis_items(items)
        self.assertEqual([item["paper_id"] for item in stale], ["pid-3"])  # only the unanalysed one
        restored = by_id["pid-1"]["translation"]
        self.assertEqual(restored, analysed("pid-1", 1)["translation"])

    def test_two_ci_cycles_keep_translations_and_ids(self):
        with clean_env():
            self.export([analysed("pid-1", 1), analysed("pid-2", 2)])
            first_cycle = database_items(self.bootstrap("one.sqlite3"))
            self.export(first_cycle)
        second_cycle = database_items(self.bootstrap("two.sqlite3"))
        self.assertEqual(get_RSS.stale_analysis_items(second_cycle), [])
        self.assertEqual(sorted(item["paper_id"] for item in second_cycle), ["pid-1", "pid-2"])
        feed = json.loads(self.json.read_text(encoding="utf-8"))
        self.assertEqual(sorted(item["title_zh"] for item in feed["items"]), ["论文 1", "论文 2"])

    def test_versioned_translation_cache_is_not_overwritten(self):
        with clean_env():
            self.export([analysed("pid-1", 1)])
        cached = dict(analysed("pid-1", 1)["translation"], zh="缓存翻译")
        (self.root / "web" / "translations.json").write_text(
            json.dumps({"Paper number 1": cached}, ensure_ascii=False), encoding="utf-8")
        items = database_items(self.bootstrap())
        self.assertEqual(items[0]["translation"]["zh"], "缓存翻译")

    def test_unanalysed_feed_defaults_are_not_mistaken_for_a_classification(self):
        self.assertIsNone(feed_translation_payload({"title_zh": "", "method": "Qualitative", "topic": "Other Marketing"}))
        payload = feed_translation_payload({"title_zh": "中文", "method": "Survey", "topic": "CSR",
                                            "methods": [], "topics": [], "classification_version": "v1"})
        self.assertEqual(payload["methods"], [{"name": "Survey", "confidence": 0.4}])
        self.assertEqual(payload["classification_version"], "v1")

    def test_keywords_are_published_only_when_not_from_the_secret(self):
        with clean_env():
            payload = self.export([analysed("pid-1", 1)])
        self.assertEqual(payload["keywords"], ["consumer", "trust"])
        with clean_env(RSS_KEYWORDS="secret topic AND private"):
            payload = self.export([analysed("pid-1", 1)], queries=("secret topic AND private",))
        self.assertNotIn("keywords", payload)
        written = self.json.read_text(encoding="utf-8")
        self.assertNotIn("secret topic", written)
        self.assertNotIn("keywords", json.loads(written))

    def test_channel_link_uses_github_repository(self):
        with clean_env():
            self.export([analysed("pid-1", 1)])
        self.assertEqual(ET.parse(self.xml).getroot().findtext("./channel/link"), DEFAULT_CHANNEL_LINK)
        with clean_env(GITHUB_REPOSITORY="octo/paper-feed"):
            self.export([analysed("pid-1", 1)])
        self.assertEqual(ET.parse(self.xml).getroot().findtext("./channel/link"), "https://github.com/octo/paper-feed")
        with clean_env(GITHUB_REPOSITORY="not a repo <script>"):
            self.export([analysed("pid-1", 1)])
        self.assertEqual(ET.parse(self.xml).getroot().findtext("./channel/link"), DEFAULT_CHANNEL_LINK)


if __name__ == "__main__":
    unittest.main()
