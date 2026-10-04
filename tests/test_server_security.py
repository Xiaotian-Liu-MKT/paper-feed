"""HTTP hardening and settings/journal helper endpoints of the local server."""
import http.client
import json
import os
import socket
import tempfile
import threading
import unittest
from unittest.mock import MagicMock, patch

import get_RSS
import server
from paper_feed.ingestion import ingest_fetch_results
from paper_feed.service import PaperFeedService


def _clean_env(**extra):
    env = {key: value for key, value in os.environ.items() if not key.startswith("OPENAI_")}
    env.update(extra)
    return env


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = self.temp.name
        self.root = root
        self.database = os.path.join(root, "paper_feed.sqlite3")
        ingest_fetch_results([{"url": "one", "success": True, "entries": [
            {"id": "g1", "title": "DOI paper", "link": "https://doi.org/10.1234/ABC.5", "summary": "", "journal": "J"},
            {"id": "g2", "title": "Plain paper", "link": "https://x.test/2", "summary": "", "journal": "J"},
        ]}], root, self.database)
        self.config_path = os.path.join(root, "config.json")
        paths = {
            "KEYWORDS_FILE": os.path.join(root, "keywords.dat"),
            "JOURNALS_FILE": os.path.join(root, "journals.dat"),
            "JOURNALS_META_FILE": os.path.join(root, "journals_meta.json"),
            "RSS_LIST_FILE": os.path.join(root, "RSS list.md"),
            "CONFIG_FILE": self.config_path,
        }
        self.patches = [patch.object(server, name, value) for name, value in paths.items()]
        self.patches.append(patch.object(get_RSS, "CONFIG_FILE", self.config_path))
        self.patches.append(patch.dict(os.environ, {"PAPER_FEED_DB": self.database, "RSS_KEYWORDS": ""}))
        for item in self.patches:
            item.start()
        self.httpd = server.socketserver.ThreadingTCPServer(("127.0.0.1", 0), server.CustomHandler)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join(timeout=2)
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def request(self, method, path, data=None, headers=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        body = raw if raw is not None else (json.dumps(data) if data is not None else None)
        merged = {"Content-Type": "application/json"} if body is not None and raw is None else {}
        merged.update(headers or {})
        conn.request(method, path, body, merged)
        response = conn.getresponse()
        text = response.read()
        conn.close()
        try:
            payload = json.loads(text or b"null")
        except ValueError:
            payload = text
        return response.status, payload

    def raw_request(self, request_bytes):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as sock:
            sock.sendall(request_bytes)
            raw = b""
            while True:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                raw += chunk
        head, _, body = raw.partition(b"\r\n\r\n")
        return int(head.split(b" ", 2)[1]), body

    def paper_ids(self):
        items = PaperFeedService(self.root, self.database).list_papers("all")
        return {item["title"]: item["paper_id"] for item in items}


class HostAndOriginTests(ServerTestCase):
    def test_foreign_host_is_rejected_for_get_and_post(self):
        self.assertEqual(self.request("GET", "/api/papers", headers={"Host": "evil.example:8000"})[0], 403)
        self.assertEqual(self.request("GET", "/index.html", headers={"Host": "evil.example"})[0], 403)
        self.assertEqual(self.request("POST", "/api/keywords/preview", {"text": "ai"},
                                      headers={"Host": "attacker.test"})[0], 403)

    def test_loopback_hosts_are_accepted(self):
        for host in (f"127.0.0.1:{self.port}", "localhost:1234", f"[::1]:{self.port}", "LOCALHOST"):
            self.assertEqual(self.request("GET", "/api/papers?view=all", headers={"Host": host})[0], 200, host)

    def test_missing_host_header_is_allowed(self):
        status, body = self.raw_request(b"GET /api/papers?view=all HTTP/1.0\r\n\r\n")
        self.assertEqual(status, 200)
        self.assertEqual(len(json.loads(body)["items"]), 2)

    def test_post_requires_json_content_type(self):
        for content_type in ("text/plain", "application/x-www-form-urlencoded", "multipart/form-data; boundary=x"):
            status, payload = self.request("POST", "/api/keywords/preview", raw='{"text": "ai"}',
                                           headers={"Content-Type": content_type})
            self.assertEqual(status, 415, content_type)
            self.assertEqual(payload["status"], "error")
        # A body without any Content-Type is also refused.
        self.assertEqual(self.request("POST", "/api/keywords/preview", raw='{"text": "ai"}')[0], 415)
        status, _ = self.request("POST", "/api/keywords/preview", raw='{"text": "ai"}',
                                 headers={"Content-Type": "application/json; charset=utf-8"})
        self.assertEqual(status, 200)

    def test_bodyless_post_without_content_type_is_allowed(self):
        with patch.object(server.JOB_RUNNER, "enqueue", return_value=({"id": "x"}, False)):
            self.assertEqual(self.request("POST", "/api/fetch")[0], 202)

    def test_origin_must_be_same_origin(self):
        body = {"text": "ai"}
        for origin in ("http://evil.example", "null", f"http://127.0.0.1:{self.port + 1}",
                       f"https://127.0.0.1:{self.port}"):
            self.assertEqual(self.request("POST", "/api/keywords/preview", body, headers={"Origin": origin})[0],
                             403, origin)
        for origin in (f"http://127.0.0.1:{self.port}", f"http://localhost:{self.port}"):
            self.assertEqual(self.request("POST", "/api/keywords/preview", body, headers={"Origin": origin})[0],
                             200, origin)
        self.assertEqual(self.request("POST", "/api/keywords/preview", body,
                                      headers={"Sec-Fetch-Site": "cross-site"})[0], 403)

    def test_oversized_body_is_rejected(self):
        status, body = self.raw_request(
            b"POST /api/keywords/preview HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
            b"Content-Length: 3000000\r\nConnection: close\r\n\r\n{}")
        self.assertEqual(status, 413)
        self.assertEqual(json.loads(body)["status"], "error")

    def test_abstract_length_is_limited(self):
        paper_id = self.paper_ids()["Plain paper"]
        status, _ = self.request("POST", "/api/update_abstract",
                                 {"paper_id": paper_id, "abstract": "x" * (server.MAX_ABSTRACT_CHARS + 1)})
        self.assertEqual(status, 400)
        self.assertEqual(self.request("POST", "/api/update_abstract", {"paper_id": paper_id, "abstract": 5})[0], 400)
        self.assertEqual(self.request("POST", "/api/update_abstract",
                                      {"paper_id": paper_id, "abstract": "short"})[0], 200)


class ConfigEndpointTests(ServerTestCase):
    def write_config(self, payload):
        with open(self.config_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle)

    def read_config(self):
        with open(self.config_path, encoding="utf-8") as handle:
            return json.load(handle)

    def test_save_config_persists_only_known_keys(self):
        self.write_config({"OPENAI_API_KEY": "keep-me", "legacy_extra": 1})
        status, _ = self.request("POST", "/api/save_config", {
            "OPENAI_API_KEY": "", "OPENAI_MODEL": " m2 ", "OPENAI_BASE_URL": "https://api.test/v1",
            "evil": "x", "PAPER_FEED_DB": "/tmp/other", "OPENAI_PROXY": 5})
        self.assertEqual(status, 200)
        self.assertEqual(self.read_config(), {"OPENAI_API_KEY": "keep-me", "legacy_extra": 1,
                                              "OPENAI_MODEL": "m2", "OPENAI_BASE_URL": "https://api.test/v1"})

    def test_clear_api_key(self):
        self.write_config({"OPENAI_API_KEY": "old-key", "OPENAI_MODEL": "m"})
        self.assertEqual(self.request("POST", "/api/save_config", {"clear_api_key": True})[0], 200)
        self.assertEqual(self.read_config(), {"OPENAI_MODEL": "m"})

    def test_config_sources_follow_precedence_without_leaking_values(self):
        self.write_config({"OPENAI_API_KEY": "file-key", "OPENAI_BASE_URL": "https://file.test",
                           "OPENAI_PROXY": "your-proxy-here"})
        with patch.dict(os.environ, _clean_env(OPENAI_API_KEY="env-key", OPENAI_BASE_URL=""), clear=True):
            status, payload = self.request("GET", "/api/config")
        self.assertEqual(status, 200)
        self.assertEqual(payload["sources"], {"OPENAI_API_KEY": "env", "OPENAI_BASE_URL": "config",
                                              "OPENAI_PROXY": "unset", "OPENAI_MODEL": "default"})
        dumped = json.dumps(payload)
        self.assertNotIn("env-key", dumped)
        self.assertNotIn("file-key", dumped)

    def test_connection_without_key(self):
        with patch.object(server, "get_config", return_value={"OPENAI_API_KEY": None, "OPENAI_MODEL": "m"}):
            status, payload = self.request("POST", "/api/test_connection", {})
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["model"], "m")
        self.assertIn("API Key", payload["error"])

    def test_connection_success_uses_minimal_request(self):
        client = MagicMock()
        client.with_options.return_value = client
        config = {"OPENAI_API_KEY": "dummy-key", "OPENAI_MODEL": "m", "OPENAI_BASE_URL": None, "OPENAI_PROXY": None}
        with patch.object(server, "get_config", return_value=config), \
                patch.object(get_RSS, "make_openai_client", return_value=client) as factory:
            status, payload = self.request("POST", "/api/test_connection", {})
        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["model"], "m")
        self.assertIsInstance(payload["latency_ms"], int)
        factory.assert_called_once_with("dummy-key", None, None)
        client.with_options.assert_called_once_with(max_retries=0, timeout=server.TEST_CONNECTION_TIMEOUT)
        self.assertEqual(client.chat.completions.create.call_args.kwargs["max_tokens"], 1)

    def test_connection_failure_redacts_key(self):
        client = MagicMock()
        client.with_options.return_value = client
        client.chat.completions.create.side_effect = RuntimeError("bad key dummy-key rejected")
        config = {"OPENAI_API_KEY": "dummy-key", "OPENAI_MODEL": "m"}
        with patch.object(server, "get_config", return_value=config), \
                patch.object(get_RSS, "make_openai_client", return_value=client):
            status, payload = self.request("POST", "/api/test_connection", {})
        self.assertEqual(status, 200)
        self.assertFalse(payload["ok"])
        self.assertNotIn("dummy-key", payload["error"])
        self.assertIn("RuntimeError", payload["error"])


class FeatureEndpointTests(ServerTestCase):
    def test_pending_summaries(self):
        status, payload = self.request("GET", "/api/summarize_favorites/pending")
        self.assertEqual((status, payload), (200, {"pending": 0, "total_favorites": 0}))
        ids = self.paper_ids()
        service = PaperFeedService(self.root, self.database)
        service.review(ids["DOI paper"], "like")
        service.review(ids["Plain paper"], "like")
        with patch.object(get_RSS, "pending_summary_items", return_value=[{"paper_id": ids["DOI paper"]}]) as pending:
            status, payload = self.request("GET", "/api/summarize_favorites/pending")
        self.assertEqual((status, payload), (200, {"pending": 1, "total_favorites": 2}))
        self.assertEqual(sorted(pending.call_args.args[1]), ["g1", "g2"])

    def test_journal_test_endpoint(self):
        result = {"url": "https://feeds.test/a", "success": True, "status_code": 200, "error": None,
                  "entries": [{"title": f"T{i}", "journal": "Feed A"} for i in range(5)]}
        with patch.object(get_RSS, "fetch_rss_result", return_value=result) as fetch:
            status, payload = self.request("POST", "/api/journals/test", {"url": " https://feeds.test/a "})
        self.assertEqual(status, 200)
        fetch.assert_called_once_with("https://feeds.test/a", retries=1)
        self.assertEqual((payload["ok"], payload["entries"], payload["feed_title"], payload["latest_titles"]),
                         (True, 5, "Feed A", ["T0", "T1", "T2"]))
        self.assertNotIn("error", payload)

        failed = {"url": "https://feeds.test/b", "success": False, "entries": [], "status_code": 404, "error": "HTTP 404"}
        with patch.object(get_RSS, "fetch_rss_result", return_value=failed):
            status, payload = self.request("POST", "/api/journals/test", {"url": "https://feeds.test/b"})
        self.assertEqual((status, payload["ok"], payload["entries"]), (200, False, 0))
        self.assertIn("HTTP 404", payload["error"])

        with patch.object(get_RSS, "fetch_rss_result") as fetch:
            for url in ("file:///etc/passwd", "javascript:alert(1)", "", None, "feeds.test/x"):
                self.assertEqual(self.request("POST", "/api/journals/test", {"url": url})[0], 400, url)
            fetch.assert_not_called()

    def test_save_journals_rejects_non_http_urls(self):
        status, payload = self.request("POST", "/api/journals", {"journals": ["https://ok.test/rss", "file:///x"]})
        self.assertEqual(status, 400)
        self.assertIn("file:///x", payload["message"])
        self.assertFalse(os.path.exists(server.JOURNALS_FILE))
        status, payload = self.request("POST", "/api/journals", {"journals": ["https://ok.test/rss", " "]})
        self.assertEqual((status, payload["journals"]), (200, ["https://ok.test/rss"]))

    def test_paper_json_includes_normalized_doi(self):
        status, payload = self.request("GET", "/api/papers?view=all")
        by_title = {item["title"]: item for item in payload["items"]}
        self.assertEqual(by_title["DOI paper"]["doi"], "10.1234/abc.5")
        self.assertIsNone(by_title["Plain paper"]["doi"])
        paper_id = by_title["DOI paper"]["paper_id"]
        self.assertEqual(self.request("GET", f"/api/papers/{paper_id}")[1]["doi"], "10.1234/abc.5")

    def test_ris_includes_doi_and_only_non_ai_abstracts(self):
        ids = self.paper_ids()
        service = PaperFeedService(self.root, self.database)
        for paper_id in ids.values():
            service.review(paper_id, "like")
        service.save_abstract(ids["DOI paper"], "Real\nabstract text")
        service._save_payload(ids["Plain paper"], "paper_analyses", "analysis_kind", "abstract",
                              {"abstract": "AI guess", "raw_abstract": "AI guess", "source": "gpt_generated"})
        ris = server.build_favorites_ris(service)["ris"]
        entries = {entry.split("TI  - ", 1)[1].split("\r\n", 1)[0]: entry for entry in ris.strip().split("\r\n\r\n")}
        self.assertIn("DO  - 10.1234/abc.5\r\n", entries["DOI paper"])
        self.assertIn("AB  - Real abstract text\r\n", entries["DOI paper"])
        self.assertNotIn("DO  - ", entries["Plain paper"])
        self.assertNotIn("AB  - ", entries["Plain paper"])


class FirstRunTests(unittest.TestCase):
    def _legacy_root(self, root):
        os.makedirs(os.path.join(root, "web"), exist_ok=True)
        with open(os.path.join(root, "web", "feed.json"), "w", encoding="utf-8") as handle:
            json.dump({"items": [{"id": "rss-1", "title": "Author's paper"}]}, handle)

    def test_server_service_starts_empty_without_importing_legacy_exports(self):
        with tempfile.TemporaryDirectory() as root:
            self._legacy_root(root)
            database = os.path.join(root, "data", "paper_feed.sqlite3")
            with patch.object(server, "BASE_DIR", root), patch.dict(os.environ, {"PAPER_FEED_DB": database}), \
                    patch("builtins.print") as printed:
                self.assertEqual(server.paper_service().list_papers("all"), [])
            self.assertTrue(os.path.exists(database))
            self.assertIn("import-legacy", " ".join(str(call.args[0]) for call in printed.call_args_list))

    def test_default_service_and_ci_bootstrap_still_import(self):
        with tempfile.TemporaryDirectory() as root:
            self._legacy_root(root)
            self.assertEqual(len(PaperFeedService(root).list_papers("all")), 1)


if __name__ == "__main__":
    unittest.main()
