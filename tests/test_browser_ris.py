import http.client
import json
import os
import tempfile
import threading
import unittest

import server
from paper_feed.db import PaperRepository, connect, now
from paper_feed.service import PaperFeedService


def write_legacy(root, items, interactions=None):
    os.makedirs(os.path.join(root, "web"), exist_ok=True)
    with open(os.path.join(root, "web", "feed.json"), "w", encoding="utf-8") as handle:
        json.dump({"items": items}, handle, ensure_ascii=False)
    if interactions is not None:
        with open(os.path.join(root, "web", "interactions.json"), "w", encoding="utf-8") as handle:
            json.dump(interactions, handle)


class FavoriteRisExportTests(unittest.TestCase):
    def _start_server(self, database):
        previous = os.environ.get("PAPER_FEED_DB")
        os.environ["PAPER_FEED_DB"] = database
        httpd = server.socketserver.ThreadingTCPServer(("127.0.0.1", 0), server.CustomHandler)
        httpd.daemon_threads = True
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return previous, httpd, thread

    def _stop_server(self, previous, httpd, thread):
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=2)
        if previous is None:
            os.environ.pop("PAPER_FEED_DB", None)
        else:
            os.environ["PAPER_FEED_DB"] = previous

    @staticmethod
    def _post(httpd):
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=3)
        conn.request("POST", "/api/export_favorites_ris", b"")
        response = conn.getresponse()
        status, headers, body = response.status, dict(response.getheaders()), response.read()
        conn.close()
        return status, headers, body

    def test_exports_only_favorites_once_with_ris_fields_and_safe_values(self):
        with tempfile.TemporaryDirectory() as root:
            items = [
                {"id": "fav", "title": "A title\r\nwith 中文", "link": "https://example.test/fav\nnext",
                 "journal": "Journal\r\nof Testing", "pub_date": "2026-04-12T10:00:00+00:00",
                 "summary": "Publication date: 2026 Source: Journal Authors(s): Ada Lovelace, 李 小龙",
                 "topic": "ignored"},
                {"id": "other", "title": "Never Export", "link": "https://example.test/other"},
            ]
            # The legacy file claims no favorites; the SQLite state set below is
            # the sole source of truth for this endpoint.
            write_legacy(root, items, {"favorites": []})
            database = os.path.join(root, "data", "paper_feed.sqlite3")
            service = PaperFeedService(root, database)
            favorite, other = service.list_papers("all")
            service.review(favorite["paper_id"], "like")
            previous, httpd, thread = self._start_server(database)
            try:
                status, headers, body = self._post(httpd)
            finally:
                self._stop_server(previous, httpd, thread)
            ris = body.decode("utf-8")
            self.assertEqual(status, 200)
            self.assertIn("application/x-research-info-systems", headers["Content-type"])
            self.assertIn(".ris", headers["Content-Disposition"])
            self.assertEqual(headers["X-Paper-Feed-Exported"], "1")
            self.assertEqual(ris.count("TY  - JOUR"), 1)
            self.assertNotIn("Never Export", ris)
            self.assertIn("TI  - A title with 中文", ris)
            self.assertIn("AU  - Lovelace, Ada", ris)
            self.assertIn("AU  - 小龙, 李", ris)
            self.assertIn("JO  - Journal of Testing", ris)
            self.assertIn("PY  - 2026", ris)
            self.assertIn("UR  - https://example.test/fav next", ris)
            self.assertIn(f"N1  - Paper Feed ID: {favorite['paper_id']}", ris)
            self.assertIn("ER  - ", ris)
            self.assertNotIn("\nwith 中文", ris)
            self.assertEqual(service.interactions()["favorites"], [favorite["paper_id"]])

    def test_uses_latest_observation_once_per_paper(self):
        with tempfile.TemporaryDirectory() as root:
            write_legacy(root, [{"id": "first", "title": "Old title", "link": "https://example.test/paper"}])
            database = os.path.join(root, "data", "paper_feed.sqlite3")
            service = PaperFeedService(root, database)
            paper_id = service.list_papers("all")[0]["paper_id"]
            service.review(paper_id, "like")
            conn = connect(database)
            with PaperRepository(conn).transaction():
                stamp = now()
                conn.execute("""INSERT INTO paper_observations
                    (paper_id,source,source_guid,link,title,journal,published_at,summary,payload_json,first_seen_at,last_seen_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""", (paper_id, "test", "newest", "https://example.test/new",
                    "Newest canonical title", "Latest Journal", "2025-01-01", "Authors(s): Grace Hopper",
                    "{}", stamp, stamp))
            conn.close()
            result = server.build_favorites_ris(service)
            self.assertEqual(result["count"], 1)
            self.assertEqual(result["ris"].count("TY  - JOUR"), 1)
            self.assertIn("TI  - Newest canonical title", result["ris"])
            self.assertNotIn("Old title", result["ris"])

    def test_empty_favorites_returns_an_empty_ris_download(self):
        with tempfile.TemporaryDirectory() as root:
            write_legacy(root, [{"id": "one", "title": "One"}], {"favorites": ["one"]})
            database = os.path.join(root, "data", "paper_feed.sqlite3")
            # Initialize/import first, then reset state so the legacy file cannot
            # influence this database-backed export.
            service = PaperFeedService(root, database)
            paper_id = service.list_papers("all")[0]["paper_id"]
            service.review(paper_id, "unlike")
            previous, httpd, thread = self._start_server(database)
            try:
                status, headers, body = self._post(httpd)
            finally:
                self._stop_server(previous, httpd, thread)
            self.assertEqual(status, 200)
            self.assertEqual(headers["X-Paper-Feed-Exported"], "0")
            self.assertEqual(body, b"")

    def test_missing_metadata_has_a_valid_deterministic_record(self):
        ris = server.build_favorite_ris_entry({"paper_id": "paper-123", "title": ""})
        self.assertEqual(ris, "TY  - JOUR\r\nTI  - Untitled\r\nN1  - Paper Feed ID: paper-123\r\nER  - ")

    def test_author_label_variants_are_bounded_and_no_author_is_empty(self):
        for label in ("Author", "Authors", "Author(s)", "Authors(s)"):
            with self.subTest(label=label):
                authors = server._ris_authors(f"Source: Test Journal {label}: Ada Lovelace, 李 小龙 Publication date: 2026")
                self.assertEqual(authors, ["Lovelace, Ada", "小龙, 李"])
        self.assertEqual(server._ris_authors("Source: Test Journal Publication date: 2026"), [])
        self.assertEqual(server._ris_authors("Author: Ada Lovelace Source: Test Journal"), ["Lovelace, Ada"])


if __name__ == "__main__":
    unittest.main()
