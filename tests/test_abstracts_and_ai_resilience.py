"""Offline tests: AI timeouts/breaker, partial batch salvage, free abstract fetching,
user-edit protection and ISO publication dates.  No real HTTP or OpenAI calls."""
import datetime
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai

import get_RSS
from ai_test_guard import setUpModule, tearDownModule  # noqa: E402,F401 (no real Codex CLI)
from paper_feed.db import connect
from paper_feed.exporter import database_items
from paper_feed.identity import normalize_published_at
from paper_feed.ingestion import ingest_fetch_results, paper_dois, save_abstracts

LONG = "This study examines how consumers respond to algorithmic recommendations " \
       "across three experiments and finds that perceived transparency increases trust."


def fake_response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def http_response(status, payload=None):
    response = MagicMock()
    response.status_code = status
    response.json.return_value = payload or {}
    return response


def timeout_error():
    return openai.APITimeoutError(request=httpx.Request("POST", "https://api.test/v1/chat/completions"))


def inverted(text):
    index = {}
    for position, word in enumerate(text.split()):
        index.setdefault(word, []).append(position)
    return index


class OpenAIClientLimitsTests(unittest.TestCase):
    def test_client_has_timeout_and_no_sdk_retries(self):
        with patch.object(openai, "OpenAI") as openai_cls:
            get_RSS.make_openai_client("key")
        kwargs = openai_cls.call_args.kwargs
        self.assertEqual(kwargs["timeout"], get_RSS.OPENAI_TIMEOUT_SECONDS)
        self.assertEqual(kwargs["max_retries"], 0)

    def test_timeout_is_not_retried_and_breaker_fails_fast(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = timeout_error()
        breaker = get_RSS.CircuitBreaker(2, "test")
        with patch.object(get_RSS.time, "sleep") as sleep:
            for _ in range(2):
                with self.assertRaises(openai.APITimeoutError):
                    get_RSS.chat_completion_with_retry(client, breaker=breaker, model="m", messages=[])
            with self.assertRaises(get_RSS.CircuitOpenError):
                get_RSS.chat_completion_with_retry(client, breaker=breaker, model="m", messages=[])
        self.assertEqual(client.chat.completions.create.call_count, 2)
        sleep.assert_not_called()

    def test_hung_endpoint_stops_batch_after_breaker_opens(self):
        client = MagicMock()
        client.chat.completions.create.side_effect = timeout_error()
        report = {}
        titles = [f"T{i}" for i in range(100)]
        with patch.object(get_RSS, "make_openai_client", return_value=client), \
             patch.object(get_RSS, "load_categories", return_value={}), \
             patch.object(get_RSS.time, "sleep"):
            self.assertEqual(get_RSS.batch_analyze_papers(titles, "key", report=report), {})
        # 10 chunks; at most one in-flight timeout per worker before the breaker opens.
        self.assertLessEqual(client.chat.completions.create.call_count, get_RSS.AI_ANALYSIS_WORKERS + 1)
        self.assertEqual(report["failed"], 100)


class BatchSalvageTests(unittest.TestCase):
    def test_align_by_index_keeps_aligned_items(self):
        chunk = ["A", "B", "C", "D"]
        results = [{"index": 3, "zh": "c"}, {"index": "1", "zh": "a"}, {"index": 99, "zh": "?"}]
        self.assertEqual(get_RSS.align_batch_results(chunk, results),
                         [("A", {"index": "1", "zh": "a"}), ("C", {"index": 3, "zh": "c"})])

    def test_old_format_equal_length_is_positional_and_mismatch_is_dropped(self):
        self.assertEqual(get_RSS.align_batch_results(["A", "B"], [{"zh": "a"}, {"zh": "b"}]),
                         [("A", {"zh": "a"}), ("B", {"zh": "b"})])
        self.assertEqual(get_RSS.align_batch_results(["A", "B", "C"], [{"zh": "a"}, {"zh": "b"}]), [])

    def test_equal_length_with_shuffled_indexes_uses_indexes(self):
        pairs = get_RSS.align_batch_results(["A", "B"], [{"index": 2, "zh": "b"}, {"index": 1, "zh": "a"}])
        self.assertEqual([(title, data["zh"]) for title, data in pairs], [("A", "a"), ("B", "b")])

    def test_batch_saves_partial_results_and_reports_rest(self):
        titles = [f"Title {i}" for i in range(10)]
        content = json.dumps({"results": [{"index": i, "zh": f"标题{i}"} for i in (2, 5, 9)]})
        client = MagicMock()
        client.chat.completions.create.return_value = fake_response(content)
        report = {}
        with patch.object(get_RSS, "make_openai_client", return_value=client), \
             patch.object(get_RSS, "load_categories", return_value={}):
            result = get_RSS.batch_analyze_papers(titles, "key", report=report)
        self.assertEqual(set(result), {"Title 1", "Title 4", "Title 8"})
        self.assertEqual(result["Title 4"]["zh"], "标题5")
        self.assertNotIn("index", result["Title 4"])
        self.assertEqual(report["failed"], 7)
        prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn('"index"', prompt)


class FreeAbstractSourceTests(unittest.TestCase):
    def test_reconstruct_inverted_index_and_clean_jats(self):
        self.assertEqual(get_RSS.reconstruct_inverted_index({"world": [1], "Hello": [0], "again": [3], "hello": [2]}),
                         "Hello world hello again")
        jats = "<jats:title>Abstract</jats:title><jats:p>Consumers &amp; brands <jats:italic>matter</jats:italic>.</jats:p>"
        self.assertEqual(get_RSS.clean_abstract_text(jats), "Consumers & brands matter.")

    def _routes(self, crossref=None, openalex=None, s2=None, calls=None):
        def fake_get(url, params=None, timeout=None, headers=None):
            if calls is not None:
                calls.append((url, params, timeout))
            if "crossref" in url:
                return crossref if not isinstance(crossref, Exception) else (_ for _ in ()).throw(crossref)
            if "openalex" in url:
                return openalex
            if "semanticscholar" in url:
                return s2
            raise AssertionError(url)
        return fake_get

    def test_order_crossref_then_openalex_then_semantic_scholar(self):
        calls = []
        routes = self._routes(crossref=http_response(200, {"message": {}}),
                              openalex=http_response(200, {"abstract_inverted_index": inverted(LONG)}),
                              s2=http_response(200, {"abstract": "unused"}), calls=calls)
        with patch.object(get_RSS.requests, "get", side_effect=routes), patch.dict(os.environ, {"OPENALEX_MAILTO": ""}):
            result = get_RSS.fetch_abstract_with_fallback({"link": "https://doi.org/10.1000/XYZ"})
        self.assertEqual(result, (LONG, "openalex", LONG))
        self.assertEqual([url.split("/")[2] for url, _, _ in calls], ["api.crossref.org", "api.openalex.org"])
        self.assertEqual(calls[1][0], "https://api.openalex.org/works/https://doi.org/10.1000/xyz")
        self.assertIsNone(calls[1][1])  # no mailto unless configured
        self.assertTrue(all(timeout is not None for _, _, timeout in calls))

    def test_crossref_wins_and_semantic_scholar_is_last_resort(self):
        routes = self._routes(crossref=http_response(200, {"message": {"abstract": f"<jats:p>{LONG}</jats:p>"}}))
        with patch.object(get_RSS.requests, "get", side_effect=routes):
            self.assertEqual(get_RSS.fetch_abstract_with_fallback({"doi": "10.1000/a"})[1], "crossref")
        routes = self._routes(crossref=http_response(404), openalex=http_response(404), s2=http_response(200, {"abstract": LONG}))
        with patch.object(get_RSS.requests, "get", side_effect=routes):
            self.assertEqual(get_RSS.fetch_abstract_with_fallback({"doi": "10.1000/a"})[1], "semantic_scholar")

    def test_mailto_only_when_configured(self):
        calls = []
        routes = self._routes(crossref=http_response(404), openalex=http_response(404), s2=http_response(404), calls=calls)
        with patch.object(get_RSS.requests, "get", side_effect=routes):
            get_RSS.fetch_abstract_with_fallback({"doi": "10.1000/a"}, mailto="me@example.org")
        self.assertEqual(calls[1][1], {"mailto": "me@example.org"})
        with patch.dict(os.environ, {"OPENALEX_MAILTO": "env@example.org"}):
            self.assertEqual(get_RSS.openalex_mailto({}), "env@example.org")
        with patch.dict(os.environ, {"OPENALEX_MAILTO": ""}):
            self.assertIsNone(get_RSS.openalex_mailto({}))

    def test_network_errors_never_raise_and_no_doi_means_no_requests(self):
        with patch.object(get_RSS.requests, "get", side_effect=get_RSS.requests.Timeout("slow")):
            self.assertEqual(get_RSS.fetch_abstract_with_fallback({"doi": "10.1000/a"}), (None, None, None))
        with patch.object(get_RSS.requests, "get", side_effect=AssertionError("no request expected")):
            self.assertEqual(get_RSS.fetch_abstract_with_fallback({"title": "T", "link": "https://example.test/x"}),
                             (None, None, None))
            self.assertEqual(get_RSS.fetch_abstract_with_fallback({"link": "https://www.sciencedirect.com/science/article/pii/S0001"}),
                             (None, None, None))

    def test_breaker_skips_a_dead_source(self):
        breakers = get_RSS.abstract_breakers()
        get_mock = MagicMock(side_effect=get_RSS.requests.ConnectionError("down"))
        with patch.object(get_RSS.requests, "get", get_mock):
            for _ in range(10):
                get_RSS.fetch_abstract_with_fallback({"doi": "10.1000/a"}, breakers=breakers)
        self.assertEqual(get_mock.call_count, 3 * get_RSS.ABSTRACT_SOURCE_BREAKER_THRESHOLD)


def _entry(number, link):
    return {"id": f"guid-{number}", "title": f"Marketing {number}", "link": link, "journal": "J", "summary": "marketing",
            "pub_date": datetime.datetime(2024, 1, number, tzinfo=datetime.timezone.utc)}


class SummarizeWithFetchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.temp.name, "data", "feed.sqlite3")
        ingest_fetch_results([{"url": "s", "success": True, "entries": [
            _entry(1, "https://doi.org/10.1000/abc"), _entry(2, "https://example.test/no-doi")]}], self.temp.name, self.db)
        self.ids = {item["id"]: item["paper_id"] for item in database_items(self.db)}
        conn = connect(self.db)
        conn.execute("UPDATE paper_review_state SET state='favorite'")
        conn.commit(); conn.close()
        self.patches = [
            patch.dict(os.environ, {"PAPER_FEED_DB": self.db, "OPENALEX_MAILTO": ""}),
            patch.object(get_RSS, "OUTPUT_FILE", os.path.join(self.temp.name, "out.xml")),
            patch.object(get_RSS, "FEED_JSON", os.path.join(self.temp.name, "web", "feed.json")),
            patch.object(get_RSS, "load_config", return_value=["marketing"]),
            patch.object(get_RSS, "fetch_abstract_with_fallback",
                         side_effect=lambda entry, **kw: (LONG, "openalex", LONG) if entry.get("doi") else (None, None, None)),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def abstract(self, guid):
        return next(item for item in database_items(self.db) if item["id"] == guid).get("abstract") or {}

    def test_doi_is_stored_as_identifier(self):
        self.assertEqual(paper_dois(self.db), {self.ids["guid-1"]: "10.1000/abc"})

    def test_without_api_key_raw_abstract_is_still_fetched(self):
        with patch.object(get_RSS, "get_config", return_value={}), \
             patch.object(get_RSS, "summarize_abstract_with_gpt") as summarize, \
             patch.object(get_RSS, "generate_abstract_with_gpt") as generate:
            result = get_RSS.summarize_specific_papers(["guid-1", "guid-2"])
        summarize.assert_not_called(); generate.assert_not_called()
        self.assertEqual((result["status"], result["fetched"]), ("ok", 1))
        saved = self.abstract("guid-1")
        self.assertEqual((saved["source"], saved["raw_abstract"], saved["abstract"]), ("openalex", LONG, LONG))
        self.assertEqual(self.abstract("guid-2"), {})
        with open(get_RSS.FEED_JSON, encoding="utf-8") as handle:
            exported = {item["id"]: item for item in json.load(handle)["items"]}
        self.assertEqual(exported["guid-1"]["abstract_source"], "openalex")

    def test_with_api_key_fetched_abstract_is_summarized(self):
        with patch.object(get_RSS, "get_config", return_value={"OPENAI_API_KEY": "test"}), \
             patch.object(get_RSS, "summarize_abstract_with_gpt", return_value="总结") as summarize, \
             patch.object(get_RSS, "generate_abstract_with_gpt", return_value="推测") as generate:
            result = get_RSS.summarize_specific_papers(["guid-1", "guid-2"])
        self.assertEqual(summarize.call_args.args[0], LONG)
        self.assertEqual(generate.call_count, 1)  # only the DOI-less paper is title-only
        self.assertEqual((result["updated"], result["fetched"], result["failed"]), (2, 1, 0))
        saved = self.abstract("guid-1")
        self.assertEqual((saved["source"], saved["abstract"], saved["raw_abstract"], saved["raw_source"]),
                         ("gpt_summarized", "总结", LONG, "openalex"))
        self.assertEqual(self.abstract("guid-2")["source"], "gpt_generated")

    def test_fetch_missing_abstracts_for_favorites(self):
        with patch.object(get_RSS, "get_config", return_value={}):
            result = get_RSS.fetch_missing_abstracts()
            again = get_RSS.fetch_missing_abstracts(view="favorite")
        self.assertEqual((result["fetched"], result["failed"], result["skipped"]), (1, 0, 1))
        self.assertEqual((again["fetched"], again["skipped"]), (0, 2))
        with patch.object(get_RSS, "get_config", return_value={}):
            self.assertEqual(get_RSS.fetch_missing_abstracts(view="inbox")["fetched"], 0)

    def test_user_edit_during_job_is_not_clobbered(self):
        paper_id = self.ids["guid-1"]
        from paper_feed.service import PaperFeedService

        def user_edits_then_summary(*args, **kwargs):
            PaperFeedService(self.temp.name, self.db, import_legacy=False).save_abstract(paper_id, "My pasted abstract")
            return "总结"

        with patch.object(get_RSS, "get_config", return_value={"OPENAI_API_KEY": "test"}), \
             patch.object(get_RSS, "summarize_abstract_with_gpt", side_effect=user_edits_then_summary), \
             patch.object(get_RSS, "generate_abstract_with_gpt", return_value="推测"):
            result = get_RSS.summarize_specific_papers(["guid-1"])
        self.assertEqual(result["updated"], 0)
        saved = self.abstract("guid-1")
        self.assertEqual((saved["source"], saved["raw_abstract"]), ("user_provided", "My pasted abstract"))


class SaveAbstractProtectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.temp.name, "data", "feed.sqlite3")
        ingest_fetch_results([{"url": "s", "success": True, "entries": [_entry(1, "https://example.test/1")]}], self.temp.name, self.db)
        self.paper_id = database_items(self.db)[0]["paper_id"]

    def tearDown(self):
        self.temp.cleanup()

    def stored(self):
        return database_items(self.db)[0]["abstract"]

    def test_user_provided_raw_is_never_replaced_by_other_text(self):
        user = {"abstract": "mine", "raw_abstract": "mine", "source": "user_provided"}
        self.assertEqual(save_abstracts(self.db, {self.paper_id: user}), 1)
        self.assertEqual(save_abstracts(self.db, {self.paper_id: {"abstract": "guess", "source": "gpt_generated"}}), 0)
        self.assertEqual(save_abstracts(self.db, {self.paper_id: {"abstract": "x", "raw_abstract": "other", "source": "gpt_summarized"}}), 0)
        self.assertEqual(save_abstracts(self.db, {self.paper_id: {"abstract": LONG, "raw_abstract": LONG, "source": "crossref"}}), 0)
        self.assertEqual(self.stored()["source"], "user_provided")
        # A summary of exactly the user's text is allowed and keeps provenance.
        self.assertEqual(save_abstracts(self.db, {self.paper_id: {"abstract": "sum", "raw_abstract": "mine", "source": "gpt_summarized"}}), 1)
        self.assertEqual((self.stored()["raw_abstract"], self.stored()["raw_source"]), ("mine", "user_provided"))
        self.assertEqual(save_abstracts(self.db, {self.paper_id: {"abstract": "g", "source": "gpt_generated"}}), 0)

    def test_fetched_raw_only_fills_gaps(self):
        self.assertEqual(save_abstracts(self.db, {self.paper_id: {"abstract": "guess", "source": "gpt_generated"}}), 1)
        self.assertEqual(save_abstracts(self.db, {self.paper_id: {"abstract": LONG, "raw_abstract": LONG, "source": "crossref"}}), 1)
        self.assertEqual(save_abstracts(self.db, {self.paper_id: {"abstract": "B", "raw_abstract": "B", "source": "openalex"}}), 0)
        self.assertEqual(self.stored()["source"], "crossref")


class PublishedAtNormalizationTests(unittest.TestCase):
    def test_normalize_published_at(self):
        self.assertEqual(normalize_published_at("Mon, 01 Jan 2024 10:00:00 +0000"), "2024-01-01T10:00:00+00:00")
        self.assertEqual(normalize_published_at("2024-03-05T01:02:03Z"), "2024-03-05T01:02:03+00:00")
        self.assertEqual(normalize_published_at("2024-03-05"), "2024-03-05T00:00:00")
        self.assertEqual(normalize_published_at("5 March 2024"), "2024-03-05")
        self.assertEqual(normalize_published_at(datetime.datetime(2024, 1, 2, 3, 4)), "2024-01-02T03:04:00")
        self.assertEqual(normalize_published_at("Spring issue"), "Spring issue")
        self.assertIsNone(normalize_published_at(""))

    def test_ingestion_stores_iso_dates(self):
        with tempfile.TemporaryDirectory() as directory:
            db = os.path.join(directory, "data", "feed.sqlite3")
            entries = [dict(_entry(1, "https://example.test/1"), pub_date="Tue, 02 Jan 2024 00:00:00 GMT"),
                       dict(_entry(2, "https://example.test/2"), pub_date="Wed, 10 Jan 2024 00:00:00 GMT"),
                       dict(_entry(3, "https://example.test/3"), pub_date="Fri, 29 Dec 2023 00:00:00 GMT")]
            ingest_fetch_results([{"url": "s", "success": True, "entries": entries}], directory, db)
            conn = connect(db)
            rows = [row[0] for row in conn.execute("SELECT published_at FROM paper_observations ORDER BY published_at DESC")]
            conn.close()
        self.assertEqual(rows, ["2024-01-10T00:00:00+00:00", "2024-01-02T00:00:00+00:00", "2023-12-29T00:00:00+00:00"])


HISTORY_XML = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>History</title>
<item><title>Historical consumer study</title><link>https://example.test/history</link><guid>history-1</guid>
<pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate><description>consumer</description><author>J</author></item>
</channel></rss>"""

BOOTSTRAP_ENV = ("CI", "GITHUB_ACTIONS", "PAPER_FEED_BOOTSTRAP_FROM_EXPORTS")


class EnsureDatabaseBootstrapTests(unittest.TestCase):
    def run_ensure(self, env):
        from paper_feed.ingestion import ensure_database
        clean = {key: value for key, value in os.environ.items() if key not in BOOTSTRAP_ENV}
        clean.update(env)
        with tempfile.TemporaryDirectory() as directory:
            with open(os.path.join(directory, "filtered_feed.xml"), "wb") as handle:
                handle.write(HISTORY_XML)
            database = os.path.join(directory, "data", "paper_feed.sqlite3")
            with patch.dict(os.environ, clean, clear=True), patch("builtins.print") as printed:
                self.assertEqual(ensure_database(directory, database), database)
            conn = connect(database)
            count = conn.execute("SELECT count(*) FROM papers").fetchone()[0]
            conn.close()
            return count, " ".join(str(arg) for call in printed.call_args_list for arg in call.args)

    def test_local_clone_gets_empty_database(self):
        count, output = self.run_ensure({"CI": "false"})
        self.assertEqual(count, 0)
        self.assertIn("Created an empty Paper Feed database", output)
        self.assertIn("python -m paper_feed import-legacy", output)

    def test_ci_or_explicit_opt_in_imports_exports(self):
        for env in ({"CI": "true"}, {"GITHUB_ACTIONS": "true"}, {"PAPER_FEED_BOOTSTRAP_FROM_EXPORTS": "1"}):
            with self.subTest(env=env):
                count, output = self.run_ensure(env)
                self.assertEqual(count, 1)
                self.assertNotIn("Created an empty", output)


class RefreshDoesNotFetchAbstractsTests(unittest.TestCase):
    def test_rss_flow_never_fetches_abstracts(self):
        entry = _entry(1, "https://doi.org/10.1000/rss")
        results = [{"url": "https://one.test/rss", "success": True, "entries": [entry]}]
        with tempfile.TemporaryDirectory() as directory, \
             patch.object(get_RSS, "WEB_DIR", directory), \
             patch.object(get_RSS, "JOURNAL_HASH_FILE", os.path.join(directory, "journals.hash")), \
             patch.dict(os.environ, {"PAPER_FEED_DB": os.path.join(directory, "paper_feed.sqlite3")}), \
             patch.object(get_RSS, "load_config", side_effect=[["https://one.test/rss"], ["marketing"]]), \
             patch.object(get_RSS, "fetch_rss_result", side_effect=results), \
             patch.object(get_RSS, "get_config", return_value={}), \
             patch.object(get_RSS, "generate_rss_xml"), \
             patch.object(get_RSS, "fetch_abstract_with_fallback", side_effect=AssertionError("no abstract fetch")) as fetch, \
             patch.object(get_RSS.requests, "get", side_effect=AssertionError("no HTTP")):
            connect(os.path.join(directory, "paper_feed.sqlite3")).close()
            outcome = get_RSS.run_rss_flow()
        self.assertTrue(outcome["published"])
        fetch.assert_not_called()


if __name__ == "__main__":
    unittest.main()
