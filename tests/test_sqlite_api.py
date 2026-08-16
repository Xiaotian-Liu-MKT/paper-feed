import json
import http.client
import os
import tempfile
import threading
import unittest
from unittest.mock import patch

import server
from paper_feed.db import PaperRepository, connect, now
from paper_feed.service import PaperFeedService, PaperNotFound


def legacy(root, feed, interactions=None):
    os.makedirs(os.path.join(root, "web"), exist_ok=True)
    with open(os.path.join(root, "web", "feed.json"), "w", encoding="utf-8") as handle:
        json.dump({"items": feed}, handle)
    if interactions is not None:
        with open(os.path.join(root, "web", "interactions.json"), "w", encoding="utf-8") as handle:
            json.dump(interactions, handle)


class SQLiteApiContractTests(unittest.TestCase):
    def test_first_open_imports_once_and_projects_views(self):
        with tempfile.TemporaryDirectory() as root:
            legacy(root, [{"id": "rss-1", "link": "https://example.test/one", "title": "One"}])
            service = PaperFeedService(root)
            paper = service.list_papers()[0]
            self.assertTrue(paper["paper_id"])
            self.assertEqual(paper["id"], "rss-1")
            backup_count = len(os.listdir(os.path.join(root, "data")))
            self.assertEqual(len(service.list_papers("all")), 1)
            self.assertEqual(len(os.listdir(os.path.join(root, "data"))), backup_count)

    def test_state_actions_are_exclusive_idempotent_and_legacy_projection_uses_paper_ids(self):
        with tempfile.TemporaryDirectory() as root:
            legacy(root, [{"id": "rss-1", "link": "https://example.test/one", "title": "One"}])
            service = PaperFeedService(root)
            paper_id = service.list_papers()[0]["paper_id"]
            for action, expected in (("like", "favorite"), ("like", "favorite"), ("archive", "archived"),
                                     ("hide", "hidden"), ("unhide", "inbox"), ("restore", "favorite"),
                                     ("unlike", "inbox")):
                self.assertEqual(service.review(paper_id, action)["state"], expected)
            self.assertEqual(service.interactions(), {"favorites": [], "archived": [], "hidden": []})
            with self.assertRaises(PaperNotFound): service.review("missing", "like")

    def test_state_cycles_record_each_real_transition_but_not_retries(self):
        with tempfile.TemporaryDirectory() as root:
            legacy(root, [{"id": "rss-1", "title": "One"}])
            service = PaperFeedService(root)
            paper_id = service.list_papers()[0]["paper_id"]
            for action in ("like", "like", "unlike", "like"):
                service.review(paper_id, action)
            conn = connect(service.database)
            self.assertEqual(conn.execute("SELECT count(*) FROM paper_review_events WHERE paper_id=?", (paper_id,)).fetchone()[0], 3)
            conn.close()

    def test_legacy_id_resolves_when_link_differs_and_writes_stay_in_sqlite(self):
        with tempfile.TemporaryDirectory() as root:
            legacy(root, [{"id": "rss-id", "link": "https://publisher.test/paper", "title": "One"}])
            interactions_path = os.path.join(root, "web", "interactions.json")
            with open(interactions_path, "w", encoding="utf-8") as handle: json.dump({"favorites": []}, handle)
            with open(interactions_path, encoding="utf-8") as handle: before = handle.read()
            service = PaperFeedService(root)
            paper_id = service.resolve_reference({"id": "rss-id"})
            self.assertEqual(paper_id, service.list_papers()[0]["paper_id"])
            service.review(paper_id, "like")
            service.save_abstract(paper_id, "A manually supplied abstract")
            self.assertEqual(service.get_paper(paper_id)["abstract"], "A manually supplied abstract")
            with open(interactions_path, encoding="utf-8") as handle: self.assertEqual(handle.read(), before)

    def test_concurrent_reviews_do_not_corrupt_state(self):
        with tempfile.TemporaryDirectory() as root:
            legacy(root, [{"id": "rss-1", "title": "One"}])
            service = PaperFeedService(root)
            paper_id = service.list_papers()[0]["paper_id"]
            errors = []
            def write(action):
                try: service.review(paper_id, action)
                except Exception as error: errors.append(error)
            threads = [threading.Thread(target=write, args=("like" if i % 2 else "archive",)) for i in range(12)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertFalse(errors)
            self.assertIn(service.get_paper(paper_id)["state"], {"favorite", "archived"})

    def test_concurrent_first_open_imports_once(self):
        with tempfile.TemporaryDirectory() as root:
            legacy(root, [{"id": "rss-1", "title": "One"}])
            database = os.path.join(root, "data", "paper_feed.sqlite3")
            errors = []
            def open_database():
                try: PaperFeedService(root, database).list_papers()
                except Exception as error: errors.append(error)
            threads = [threading.Thread(target=open_database) for _ in range(2)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertFalse(errors)
            conn = connect(database)
            self.assertEqual(conn.execute("SELECT count(*) FROM fetch_runs").fetchone()[0], 1)
            conn.close()
            self.assertEqual(len(list(__import__("pathlib").Path(root, "data").glob("paper_feed-legacy-backup-*"))), 1)

    def test_favorite_summary_ids_choose_one_latest_observation_per_paper(self):
        with tempfile.TemporaryDirectory() as root:
            legacy(root, [{"id": "rss-1", "title": "One"}])
            service = PaperFeedService(root)
            paper_id = service.list_papers()[0]["paper_id"]
            conn = connect(service.database)
            repo = PaperRepository(conn)
            with repo.transaction(): repo.add_legacy_alias(paper_id, "rss-older")
            conn.close()
            service.review(paper_id, "like")
            self.assertEqual(service.favorite_legacy_ids(), [(paper_id, "rss-1")])

    def test_preference_report_uses_sqlite_not_legacy_interactions(self):
        with tempfile.TemporaryDirectory() as root:
            legacy(root, [{"id": "rss-1", "title": "One"}], {"favorites": []})
            database = os.path.join(root, "data", "paper_feed.sqlite3")
            service = PaperFeedService(root, database)
            service.review(service.list_papers()[0]["paper_id"], "like")
            prior_db, prior_report, prior_interactions = os.environ.get("PAPER_FEED_DB"), server.REPORT_FILE, server.INTERACTIONS_FILE
            os.environ["PAPER_FEED_DB"] = database
            server.REPORT_FILE, server.INTERACTIONS_FILE = os.path.join(root, "report.json"), os.path.join(root, "does-not-exist.json")
            try:
                report = server.generate_title_report()
                self.assertEqual((report["status"], report["report"]["counts"]["favorites"]), ("ok", 1))
            finally:
                server.REPORT_FILE, server.INTERACTIONS_FILE = prior_report, prior_interactions
                if prior_db is None: os.environ.pop("PAPER_FEED_DB", None)
                else: os.environ["PAPER_FEED_DB"] = prior_db

    def test_analysis_projection_keeps_translation_and_applies_partial_correction(self):
        with tempfile.TemporaryDirectory() as root:
            legacy(root, [{"id": "rss-1", "title": "One"}])
            service = PaperFeedService(root)
            paper_id = service.list_papers()[0]["paper_id"]
            conn = connect(service.database)
            with PaperRepository(conn).transaction():
                conn.execute("""INSERT INTO paper_analyses(paper_id,analysis_kind,analysis_version,payload_json,updated_at)
                    VALUES (?,'translation','',?,?)""", (paper_id, json.dumps({
                        "zh": "中文标题", "methods": [{"name": "Experiment", "confidence": .9}],
                        "topics": [{"name": "Consumer Behavior", "confidence": .8}], "theories": ["T1"],
                        "context": ["online"], "subjects": ["adults"], "novelty_score": .7,
                        "classification_version": "v3"}), now()))
                conn.execute("""INSERT INTO paper_analyses(paper_id,analysis_kind,analysis_version,payload_json,updated_at)
                    VALUES (?,'abstract','',?,?)""", (paper_id, json.dumps({"abstract": "Summary", "raw_abstract": "Raw", "source": "gpt"}), now()))
                conn.execute("""INSERT INTO paper_user_overrides(paper_id,override_kind,payload_json,updated_at)
                    VALUES (?,'user_correction',?,?)""", (paper_id, json.dumps({"methods": [{"name": "Survey"}], "topics": [], "theories": []}), now()))
            conn.close()
            item = service.get_paper(paper_id)
            self.assertEqual((item["title_zh"], item["method"], item["topic"]), ("中文标题", "Survey", "Consumer Behavior"))
            self.assertEqual((item["theories"], item["context"], item["classification_version"]), (["T1"], ["online"], "v3"))
            self.assertEqual((item["abstract"], item["abstract_source"], item["classification_source"], item["user_corrected"]), ("Summary", "gpt", "user", True))

    def test_summarize_job_does_not_reimport_stale_legacy_abstract_cache(self):
        with tempfile.TemporaryDirectory() as root:
            legacy(root, [{"id": "rss-1", "title": "One"}])
            database = os.path.join(root, "data", "paper_feed.sqlite3")
            service = PaperFeedService(root, database)
            paper_id = service.list_papers()[0]["paper_id"]
            service.review(paper_id, "like")
            # A stale cache is intentionally present, but must never be read by
            # server.run_summarize_job after direct SQLite summarization.
            with open(os.path.join(root, "web", "abstracts.json"), "w", encoding="utf-8") as handle:
                json.dump({"rss-1": {"abstract": "OLD CACHE", "source": "legacy"}}, handle)
            prior = os.environ.get("PAPER_FEED_DB")
            os.environ["PAPER_FEED_DB"] = database
            def direct_sqlite_summary(ids):
                self.assertEqual(ids, ["rss-1"])
                service.save_abstract(paper_id, "NEW SQLITE")
                return {"status": "ok", "summarized": 1}
            try:
                with patch.object(server, "summarize_specific_papers", side_effect=direct_sqlite_summary) as summarize:
                    self.assertEqual(server.run_summarize_job()["status"], "ok")
                summarize.assert_called_once_with(["rss-1"])
                self.assertEqual(service.get_paper(paper_id)["abstract"], "NEW SQLITE")
            finally:
                if prior is None: os.environ.pop("PAPER_FEED_DB", None)
                else: os.environ["PAPER_FEED_DB"] = prior

    def test_minimal_http_contract(self):
        with tempfile.TemporaryDirectory() as root:
            legacy(root, [{"id": "rss-1", "link": "https://example.test/one", "title": "One"}])
            database = os.path.join(root, "data", "paper_feed.sqlite3")
            initial = PaperFeedService(root, database).list_papers()[0]
            conn = connect(database)
            with PaperRepository(conn).transaction():
                conn.execute("""INSERT INTO paper_analyses(paper_id,analysis_kind,analysis_version,payload_json,updated_at)
                    VALUES (?,'translation','',?,?)""", (initial["paper_id"], json.dumps({"zh": "中文", "methods": ["Experiment"], "classification_version": "v1"}), now()))
            conn.close()
            prior = os.environ.get("PAPER_FEED_DB")
            os.environ["PAPER_FEED_DB"] = database
            httpd = server.socketserver.ThreadingTCPServer(("127.0.0.1", 0), server.CustomHandler)
            httpd.daemon_threads = True
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                def request(method, path, data=None):
                    conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=3)
                    body = json.dumps(data) if data is not None else None
                    conn.request(method, path, body, {"Content-Type": "application/json"} if body else {})
                    response = conn.getresponse(); payload = json.loads(response.read()); conn.close()
                    return response.status, payload
                status, payload = request("GET", "/api/papers?view=inbox")
                self.assertEqual((status, payload["items"][0]["paper_id"], payload["items"][0]["title_zh"], payload["items"][0]["method"]), (200, initial["paper_id"], "中文", "Experiment"))
                self.assertEqual(request("POST", f"/api/papers/{initial['paper_id']}/review", {"action": "like"})[0], 200)
                status, payload = request("POST", "/api/interactions", {"paper_id": initial["paper_id"], "action": "archive"})
                self.assertEqual((status, payload["archived"]), (200, [initial["paper_id"]]))
                self.assertEqual(request("GET", "/api/interactions")[1]["archived"], [initial["paper_id"]])
                self.assertEqual(request("POST", "/api/papers/missing/review", {"action": "like"})[0], 404)
            finally:
                httpd.shutdown(); httpd.server_close(); thread.join(timeout=2)
                if prior is None: os.environ.pop("PAPER_FEED_DB", None)
                else: os.environ["PAPER_FEED_DB"] = prior


if __name__ == "__main__":
    unittest.main()
