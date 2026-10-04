import http.client
import json
import os
import sqlite3
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx
import openai

import get_RSS
import server
from paper_feed.backup import backup_database
from paper_feed.ingestion import ingest_fetch_results


def fake_response(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


def fake_client(side_effect):
    client = MagicMock()
    client.chat.completions.create.side_effect = side_effect
    return client


class OpenAIClientTests(unittest.TestCase):
    def test_proxy_uses_httpx_proxy_keyword(self):
        with patch.object(httpx, "Client") as client_cls, patch.object(openai, "OpenAI") as openai_cls:
            get_RSS.make_openai_client("key", None, "http://127.0.0.1:9")
        client_cls.assert_called_once_with(proxy="http://127.0.0.1:9")
        self.assertNotIn("proxies", client_cls.call_args.kwargs)
        self.assertIs(openai_cls.call_args.kwargs["http_client"], client_cls.return_value)

    def test_real_httpx_accepts_proxy(self):
        # httpx 0.28 removed `proxies=`; this must construct without a TypeError.
        client = get_RSS.make_openai_client("key", None, "http://127.0.0.1:9")
        self.assertIsNotNone(client)

    def test_client_creation_failure_does_not_raise(self):
        report = {}
        with patch.object(get_RSS, "make_openai_client", side_effect=TypeError("bad proxy")):
            result = get_RSS.batch_analyze_papers(["A", "B"], "key", proxy="bogus", report=report)
        self.assertEqual(result, {})
        self.assertEqual(report["failed"], 2)
        self.assertTrue(report["errors"])

    def test_retry_on_transient_errors_then_success(self):
        request = httpx.Request("POST", "https://api.test/v1/chat/completions")
        rate_limited = openai.RateLimitError("slow down", response=httpx.Response(429, request=request), body=None)
        connection = openai.APIConnectionError(request=request)
        client = fake_client([rate_limited, connection, fake_response("ok")])
        with patch.object(get_RSS.time, "sleep") as sleep:
            response = get_RSS.chat_completion_with_retry(client, model="m", messages=[])
        self.assertEqual(response.choices[0].message.content, "ok")
        self.assertEqual(client.chat.completions.create.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertLess(sleep.call_args_list[0].args[0], sleep.call_args_list[1].args[0])

    def test_server_error_is_retried_but_client_error_is_not(self):
        request = httpx.Request("POST", "https://api.test/v1/chat/completions")
        server_error = openai.InternalServerError("boom", response=httpx.Response(503, request=request), body=None)
        client = fake_client([server_error, fake_response("ok")])
        with patch.object(get_RSS.time, "sleep"):
            get_RSS.chat_completion_with_retry(client, model="m", messages=[])
        auth = openai.AuthenticationError("bad key", response=httpx.Response(401, request=request), body=None)
        client = fake_client([auth, fake_response("ok")])
        with patch.object(get_RSS.time, "sleep"), self.assertRaises(openai.AuthenticationError):
            get_RSS.chat_completion_with_retry(client, model="m", messages=[])
        self.assertEqual(client.chat.completions.create.call_count, 1)

    def test_batch_reports_failed_chunks_and_uses_configured_model(self):
        good = json.dumps({"results": [{"zh": "一", "methods": [{"name": "Experiment"}], "topics": []}]})
        client = fake_client([fake_response(good)])
        categories = {"methods": [{"name": "Experiment"}], "topics": [{"name": "AI"}]}
        report = {}
        with patch.object(get_RSS, "make_openai_client", return_value=client), \
             patch.object(get_RSS, "load_categories", return_value=categories):
            result = get_RSS.batch_analyze_papers(["Only title"], "key", model="my-model", report=report)
        self.assertEqual(client.chat.completions.create.call_args.kwargs["model"], "my-model")
        # Unknown/empty topic falls back to Unclassified (no "Other Marketing" configured).
        self.assertEqual(result["Only title"]["topics"][0]["name"], get_RSS.UNCLASSIFIED_LABEL)
        self.assertEqual(report["failed"], 0)

        client = fake_client(ValueError("invalid json"))
        report = {}
        with patch.object(get_RSS, "make_openai_client", return_value=client), \
             patch.object(get_RSS, "load_categories", return_value=categories):
            result = get_RSS.batch_analyze_papers([f"T{i}" for i in range(12)], "key", report=report)
        self.assertEqual(result, {})
        self.assertEqual(report["failed"], 12)
        self.assertEqual(len(report["errors"]), 2)

    def test_domain_comes_from_categories(self):
        client = fake_client([fake_response(json.dumps({"results": [{"zh": "x"}]}))])
        with patch.object(get_RSS, "make_openai_client", return_value=client), \
             patch.object(get_RSS, "load_categories", return_value={"domain": "Accounting"}):
            get_RSS.batch_analyze_papers(["T"], "key")
        prompt = client.chat.completions.create.call_args.kwargs["messages"][1]["content"]
        self.assertIn("expert in Accounting", prompt)

    def test_title_only_summary_is_low_temperature_and_speculative(self):
        client = fake_client([fake_response("guess")])
        with patch.object(get_RSS, "make_openai_client", return_value=client):
            self.assertEqual(get_RSS.generate_abstract_with_gpt("T", "J", "key", model="m"), "guess")
        kwargs = client.chat.completions.create.call_args.kwargs
        self.assertLessEqual(kwargs["temperature"], 0.3)
        self.assertIn("title only", kwargs["messages"][1]["content"])
        self.assertIn("NOT read", kwargs["messages"][1]["content"])

    def test_fallback_label(self):
        self.assertEqual(get_RSS.fallback_label([], "Qualitative"), "Qualitative")
        self.assertEqual(get_RSS.fallback_label(["Qualitative", "Experiment"], "Qualitative"), "Qualitative")
        self.assertEqual(get_RSS.fallback_label(["Experiment"], "Qualitative"), "Unclassified")


class ConfigPrecedenceTests(unittest.TestCase):
    def config_with(self, file_values, env_values):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(file_values, handle)
            clean_env = {key: value for key, value in os.environ.items() if key not in get_RSS.CONFIG_KEYS}
            clean_env.update(env_values)
            with patch.object(get_RSS, "CONFIG_FILE", path), patch.dict(os.environ, clean_env, clear=True):
                return get_RSS.get_config()

    def test_env_overrides_file_and_file_overrides_default(self):
        config = self.config_with({"OPENAI_API_KEY": "file-key", "OPENAI_MODEL": "file-model", "EXTRA": 1},
                                  {"OPENAI_API_KEY": "env-key"})
        self.assertEqual(config["OPENAI_API_KEY"], "env-key")
        self.assertEqual(config["OPENAI_MODEL"], "file-model")
        self.assertEqual(config["EXTRA"], 1)

    def test_empty_and_placeholder_values_are_ignored(self):
        config = self.config_with({"OPENAI_API_KEY": "your-api-key-here", "OPENAI_BASE_URL": "",
                                   "OPENAI_PROXY": "your-proxy"},
                                  {"OPENAI_API_KEY": "", "OPENAI_MODEL": "  "})
        self.assertIsNone(config["OPENAI_API_KEY"])
        self.assertIsNone(config["OPENAI_BASE_URL"])
        self.assertIsNone(config["OPENAI_PROXY"])
        self.assertEqual(config["OPENAI_MODEL"], "gpt-4o-mini")

    def test_empty_env_falls_back_to_file(self):
        config = self.config_with({"OPENAI_API_KEY": "file-key"}, {"OPENAI_API_KEY": ""})
        self.assertEqual(config["OPENAI_API_KEY"], "file-key")


class KeywordMatcherTests(unittest.TestCase):
    def match(self, text, *rules):
        return get_RSS.match_entry({"title": text, "summary": ""}, list(rules))

    def test_word_start_boundary_case_insensitive(self):
        self.assertFalse(self.match("He said hello", "ai"))
        self.assertTrue(self.match("Generative AI in retail", "ai"))
        self.assertTrue(self.match("Consumer choice", "consum"))
        self.assertFalse(self.match("Overconsumption", "consum"))

    def test_and_or_and_exclusion(self):
        title = "Consumer behavior of children on social media"
        self.assertTrue(self.match(title, "consumer and social media"))
        self.assertFalse(self.match(title, "consumer AND pricing"))
        self.assertTrue(self.match(title, "pricing", "social media"))  # lines are OR'd
        self.assertFalse(self.match(title, "consumer AND -children"))
        self.assertFalse(self.match(title, "consumer AND NOT children"))
        self.assertTrue(self.match(title, "consumer AND NOT adults"))
        self.assertFalse(self.match(title, "-children"))  # exclusion-only rule matches nothing

    def test_quoted_phrases_and_comments(self):
        self.assertTrue(self.match("Research and development spending", '"research and development"'))
        self.assertFalse(self.match("Research on development", '"research and development"'))
        self.assertFalse(self.match("anything", "# anything"))
        self.assertTrue(self.match("Social   media use", "social media"))

    def test_legacy_lines_still_work(self):
        self.assertTrue(self.match("Consumer embarrassment at checkout", "embarrassment"))
        self.assertTrue(self.match("Fundraising appeals", "fundraising"))
        self.assertTrue(get_RSS.match_entry({"title": "Brand trust", "summary": "consumer behavior"},
                                            ["brand AND consumer behavior"]))

    def test_cjk_terms_match_without_word_boundaries(self):
        self.assertTrue(self.match("研究消费者行为", "消费"))

    def test_load_config_skips_comments_and_reports_env_override(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "keywords.dat")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("# comment\n  # indented comment\nbrand\n\n")
            with patch.dict(os.environ, {"RSS_KEYWORDS": ""}):
                self.assertEqual(get_RSS.load_config(path, "RSS_KEYWORDS"), ["brand"])
            with patch.dict(os.environ, {"RSS_KEYWORDS": "a;b"}), patch("builtins.print") as printed:
                self.assertEqual(get_RSS.load_config(path, "RSS_KEYWORDS"), ["a", "b"])
            self.assertIn("OVERRIDES", printed.call_args.args[0])

    def test_display_terms(self):
        self.assertEqual(get_RSS.keyword_display_terms(["brand AND -kids", '"r and d"']), ["brand", "r and d"])


class JobStatusTests(unittest.TestCase):
    def wait(self, runner, job_id, done=("succeeded", "failed", "partial_failed")):
        for _ in range(100):
            job = runner.get(job_id)
            if job["status"] in done:
                return job
            time.sleep(0.01)
        return runner.get(job_id)

    def test_failed_items_mark_partial_failed(self):
        runner = server.JobRunner()
        job, _ = runner.enqueue("reanalyze", lambda: {"status": "ok", "updated": 3, "failed": 2, "errors": ["x"]})
        job = self.wait(runner, job["id"])
        self.assertEqual(job["status"], "partial_failed")
        self.assertEqual(job["result"]["failed"], 2)

    def test_nothing_succeeded_marks_failed(self):
        runner = server.JobRunner()
        job, _ = runner.enqueue("summarize", lambda: {"status": "ok", "updated": 0, "failed": 1, "errors": ["x"]})
        self.assertEqual(self.wait(runner, job["id"])["status"], "failed")

    def test_clean_result_succeeds_and_jobs_are_listed_newest_first(self):
        runner = server.JobRunner()
        first, _ = runner.enqueue("a", lambda: {"status": "ok", "updated": 0, "failed": 0})
        second, _ = runner.enqueue("b", lambda: {"published": True, "successful_sources": ["x"], "failed": 0})
        self.assertEqual(self.wait(runner, first["id"])["status"], "succeeded")
        self.assertEqual(self.wait(runner, second["id"])["status"], "succeeded")
        self.assertEqual([job["id"] for job in runner.list()], [second["id"], first["id"]])

    def test_main_exit_codes(self):
        with patch.object(get_RSS, "run_rss_flow", return_value={"published": True, "failed_sources": ["x"]}):
            self.assertEqual(get_RSS.main([]), 0)
        with patch.object(get_RSS, "run_rss_flow", return_value={"published": False, "successful_sources": []}):
            self.assertEqual(get_RSS.main([]), 1)
        with patch.object(get_RSS, "run_rss_flow", return_value={"published": False, "config_error": True}):
            self.assertEqual(get_RSS.main([]), 2)

    def test_empty_config_returns_error_result(self):
        with patch.object(get_RSS, "load_config", side_effect=[["https://x.test/rss"], []]):
            result = get_RSS.run_rss_flow()
        self.assertEqual(result["status"], "error")
        self.assertTrue(result["config_error"])
        self.assertFalse(result["published"])


class HelperTests(unittest.TestCase):
    def test_strip_tracking_params(self):
        self.assertEqual(server.strip_tracking_params("https://a.test/rss?utm_source=x&jc=mksc&UTM_medium=y#top"),
                         "https://a.test/rss?jc=mksc#top")
        self.assertEqual(server.strip_tracking_params("https://a.test/rss?utm_source=x"), "https://a.test/rss")
        self.assertEqual(server.strip_tracking_params(" https://a.test/rss?a=%2F "), "https://a.test/rss?a=%2F")

    def test_resolve_port(self):
        with patch.dict(os.environ, {"PAPER_FEED_PORT": "9123"}):
            self.assertEqual(server.resolve_port(), 9123)
            self.assertEqual(server.resolve_port(8100), 8100)
        with patch.dict(os.environ, {"PAPER_FEED_PORT": "nope"}), patch("builtins.print"):
            self.assertEqual(server.resolve_port(), 8000)

    def test_windows_does_not_reuse_address(self):
        self.assertEqual(server.PaperFeedHTTPServer.allow_reuse_address, os.name != "nt")

    def test_backup_uses_sqlite_backup_api(self):
        with tempfile.TemporaryDirectory() as directory:
            database = os.path.join(directory, "paper_feed.sqlite3")
            ingest_fetch_results([{"url": "one", "success": True, "entries": [
                {"id": "g1", "title": "One", "link": "https://x.test/1", "summary": "", "journal": "J"}]}],
                directory, database)
            result = backup_database(database, os.path.join(directory, "backups"))
            self.assertEqual((result["integrity"], result["papers"]), ("ok", 1))
            conn = sqlite3.connect(result["backup"])
            self.assertEqual(conn.execute("SELECT count(*) FROM papers").fetchone()[0], 1)
            conn.close()


RSS_LIST = """# RSS list

## Marketing

- Journal of Testing
   标签: AJG 4*，FT50, UTD24
   RSS: `https://feeds.test/jt?jc=jt`
- Second Journal
   RSS: `https://feeds.test/second`
"""


class HttpApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = self.temp.name
        self.database = os.path.join(root, "paper_feed.sqlite3")
        ingest_fetch_results([{"url": "one", "success": True, "entries": [
            {"id": "g1", "title": "AI shopping assistants", "link": "https://x.test/1", "summary": "consumer study", "journal": "J"},
            {"id": "g2", "title": "He said nothing about pricing", "link": "https://x.test/2", "summary": "", "journal": "J"},
        ]}], root, self.database)
        paths = {
            "KEYWORDS_FILE": os.path.join(root, "keywords.dat"),
            "JOURNALS_FILE": os.path.join(root, "journals.dat"),
            "JOURNALS_META_FILE": os.path.join(root, "journals_meta.json"),
            "RSS_LIST_FILE": os.path.join(root, "RSS list.md"),
        }
        with open(paths["RSS_LIST_FILE"], "w", encoding="utf-8") as handle:
            handle.write(RSS_LIST)
        with open(paths["JOURNALS_FILE"], "w", encoding="utf-8") as handle:
            handle.write("https://feeds.test/jt?jc=jt&utm_source=newsletter\n")
        with open(paths["KEYWORDS_FILE"], "w", encoding="utf-8") as handle:
            handle.write("# my rules\nai AND consumer\n")
        self.patches = [patch.object(server, name, value) for name, value in paths.items()]
        self.patches.append(patch.dict(os.environ, {"PAPER_FEED_DB": self.database, "RSS_KEYWORDS": ""}))
        for item in self.patches:
            item.start()
        self.httpd = server.socketserver.ThreadingTCPServer(("127.0.0.1", 0), server.CustomHandler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join(timeout=2)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        body = raw if raw is not None else (json.dumps(data) if data is not None else None)
        conn.request(method, path, body, headers or ({"Content-Type": "application/json"} if body else {}))
        response = conn.getresponse()
        content_type = response.getheader("Content-Type") or ""
        payload = json.loads(response.read() or b"null")
        conn.close()
        return response.status, payload, content_type

    def test_keywords_get_post_and_preview(self):
        status, payload, content_type = self.request("GET", "/api/keywords")
        self.assertEqual((status, payload["keywords"]), (200, ["ai AND consumer"]))
        self.assertIn("# my rules", payload["text"])
        self.assertIn("application/json", content_type)

        status, payload, _ = self.request("POST", "/api/keywords", {"text": "# c\npricing\nai"})
        self.assertEqual((status, payload["keywords"]), (200, ["pricing", "ai"]))
        with open(server.KEYWORDS_FILE, encoding="utf-8") as handle:
            self.assertEqual(handle.read(), "# c\npricing\nai\n")

        status, payload, _ = self.request("POST", "/api/keywords/preview", {"text": "ai\n# ignored\nretail AND -x"})
        self.assertEqual(status, 200)
        self.assertEqual((payload["total_papers"], payload["matched"]), (2, 1))
        self.assertEqual(payload["terms"], [{"term": "ai", "count": 1}, {"term": "retail", "count": 0}])
        self.assertEqual(payload["samples"][0]["title"], "AI shopping assistants")
        self.assertTrue(payload["samples"][0]["paper_id"])

        self.assertEqual(self.request("POST", "/api/keywords", {"text": 5})[0], 400)

    def test_journal_catalog_and_utm_stripping(self):
        status, payload, _ = self.request("GET", "/api/journal_catalog")
        self.assertEqual(status, 200)
        first, second = payload["items"]
        self.assertEqual(first, {"name": "Journal of Testing", "url": "https://feeds.test/jt?jc=jt",
                                 "subject": "Marketing", "tags": ["AJG 4*", "FT50", "UTD24"], "subscribed": True})
        self.assertEqual((second["tags"], second["subscribed"]), ([], False))

        status, payload, _ = self.request("POST", "/api/journals", {
            "journals": ["https://feeds.test/second?utm_source=a&utm_campaign=b", "https://feeds.test/second"],
            "meta": {"https://feeds.test/second?utm_source=a&utm_campaign=b": {"name": "Second"}}})
        self.assertEqual(status, 200)
        self.assertEqual(payload["journals"], ["https://feeds.test/second"])
        self.assertEqual(payload["meta"], {"https://feeds.test/second": {"name": "Second"}})

    def test_config_jobs_and_json_errors(self):
        with patch.object(server, "get_config", return_value={"OPENAI_API_KEY": "secret", "OPENAI_MODEL": "m1"}):
            status, payload, _ = self.request("GET", "/api/config")
        self.assertEqual((payload["has_api_key"], payload["api_key_configured"], payload["OPENAI_MODEL"]), (True, True, "m1"))
        self.assertNotIn("secret", json.dumps(payload))

        status, payload, _ = self.request("GET", "/api/jobs")
        self.assertEqual(status, 200)
        self.assertIsInstance(payload["jobs"], list)

        status, payload, content_type = self.request("GET", "/api/does-not-exist")
        self.assertEqual((status, payload["status"]), (404, "error"))
        self.assertIn("application/json", content_type)
        status, payload, content_type = self.request("POST", "/api/does-not-exist", {})
        self.assertEqual((status, payload["status"]), (404, "error"))
        # Missing Content-Length is treated as an empty body, not a crash.
        import socket
        with socket.create_connection(("127.0.0.1", self.httpd.server_address[1]), timeout=5) as sock:
            sock.sendall(b"POST /api/keywords/preview HTTP/1.1\r\nHost: localhost\r\nConnection: close\r\n\r\n")
            raw = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                raw += chunk
        head, _, body = raw.partition(b"\r\n\r\n")
        self.assertIn(b" 400 ", head.split(b"\r\n")[0] + b" ")
        self.assertIn(b"application/json", head)
        self.assertEqual(json.loads(body)["status"], "error")
        status, payload, _ = self.request("POST", "/api/keywords", raw="{not json")
        self.assertEqual(status, 400)

    def test_save_config_accepts_model(self):
        config_path = os.path.join(self.temp.name, "config.json")
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump({"OPENAI_API_KEY": "keep-me"}, handle)
        with patch.object(server, "CONFIG_FILE", config_path):
            status, payload, _ = self.request("POST", "/api/save_config", {"OPENAI_API_KEY": "", "OPENAI_MODEL": " gpt-x "})
        self.assertEqual(status, 200)
        with open(config_path, encoding="utf-8") as handle:
            self.assertEqual(json.load(handle), {"OPENAI_API_KEY": "keep-me", "OPENAI_MODEL": "gpt-x"})


if __name__ == "__main__":
    unittest.main()
