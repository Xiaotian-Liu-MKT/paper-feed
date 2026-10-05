"""HTTP contract for the AI taste-profile endpoints (server.py).

The taste model/flows (``paper_feed.taste`` and the ``get_RSS`` taste flows) are
replaced with fakes so these tests exercise only the HTTP layer and never call AI.
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

import get_RSS
import server
from ai_test_guard import setUpModule, tearDownModule  # noqa: E402,F401 (no real Codex CLI)
from paper_feed.service import PaperFeedService


def legacy(root, feed):
    os.makedirs(os.path.join(root, "web"), exist_ok=True)
    with open(os.path.join(root, "web", "feed.json"), "w", encoding="utf-8") as handle:
        json.dump({"items": feed}, handle)


def fake_taste_module(store):
    """In-memory stand-in for paper_feed.taste following the shared contract."""
    module = types.ModuleType("paper_feed.taste")
    module.TASTE_MIN_SAMPLES = 10

    def load_profile(database):
        store.setdefault("databases", []).append(database)
        return store.get("profile")

    def save_profile(database, profile, source, model="", sample_counts=None):
        if not isinstance(profile, dict) or not any(profile.get(key) for key in server.TASTE_CONTENT_FIELDS):
            raise ValueError("profile is empty")
        stored = dict(profile, version="abc123def456", source=source, model=model,
                      created_at="2026-10-05T00:00:00", sample_counts=sample_counts)
        store["profile"] = stored
        return stored

    module.load_profile = load_profile
    module.save_profile = save_profile
    module.sample_counts = lambda database: {"favorite": 3, "archived": 2, "hidden": 4}
    module.samples_since_profile = lambda database, profile: 7
    module.save_scores = lambda database, scores, profile_version: len(scores)
    return module


class TasteApiTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        legacy(root, [{"id": "rss-1", "title": "One"}, {"id": "rss-2", "title": "Two"},
                      {"id": "rss-3", "title": "Three"}])
        self.database = os.path.join(root, "data", "paper_feed.sqlite3")
        service = PaperFeedService(root, self.database)
        papers = service.list_papers()
        service.review(papers[0]["paper_id"], "like")
        self.inbox_ids = [paper["paper_id"] for paper in papers[1:]]

        self._prior_db = os.environ.get("PAPER_FEED_DB")
        os.environ["PAPER_FEED_DB"] = self.database
        self.store = {}
        self._patches = [
            patch.dict(sys.modules, {"paper_feed.taste": fake_taste_module(self.store)}),
            patch.object(get_RSS, "ai_settings", return_value={"ready": True, "reason": None}),
            patch.object(server, "JOB_RUNNER", server.JobRunner()),
        ]
        for item in self._patches:
            item.start()

        self.httpd = server.socketserver.ThreadingTCPServer(("127.0.0.1", 0), server.CustomHandler)
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=2)
        for item in reversed(self._patches):
            item.stop()
        if self._prior_db is None:
            os.environ.pop("PAPER_FEED_DB", None)
        else:
            os.environ["PAPER_FEED_DB"] = self._prior_db
        self._tmp.cleanup()

    def request(self, method, path, data=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        body = raw if raw is not None else (json.dumps(data) if data is not None else None)
        conn.request(method, path, body, {"Content-Type": "application/json"} if body is not None else {})
        response = conn.getresponse()
        payload = json.loads(response.read())
        conn.close()
        return response.status, payload

    def wait_job(self, job_id):
        for _ in range(100):
            status, job = self.request("GET", f"/api/jobs/{job_id}")
            self.assertEqual(status, 200)
            if job["status"] not in {"queued", "running"}:
                return job
            time.sleep(0.02)
        self.fail("job did not finish")

    def test_get_profile_without_profile(self):
        with patch.object(get_RSS, "ai_settings", return_value={"ready": False, "reason": "no backend"}):
            status, payload = self.request("GET", "/api/taste_profile")
        self.assertEqual(status, 200)
        self.assertEqual(payload, {"profile": None, "counts": {"favorite": 3, "archived": 2, "hidden": 4},
                                   "new_samples": 0, "min_samples": 10, "ai_ready": False,
                                   "ai_reason": "no backend"})
        self.assertEqual(self.store["databases"], [self.database])

    def test_save_then_get_profile(self):
        profile = {"summary": "偏好消费者行为实验", "likes": ["AI 与消费者"], "dislikes": [],
                   "boundaries": [], "methods": ["Experiment"], "version": "ignored", "source": "ai"}
        status, payload = self.request("POST", "/api/taste_profile/save", {"profile": profile})
        self.assertEqual(status, 200)
        self.assertEqual(payload["status"], "ok")
        saved = payload["profile"]
        self.assertEqual((saved["source"], saved["model"], saved["version"]), ("user", "", "abc123def456"))
        self.assertEqual(saved["sample_counts"], {"favorite": 3, "archived": 2, "hidden": 4})
        self.assertEqual(saved["likes"], ["AI 与消费者"])

        status, payload = self.request("GET", "/api/taste_profile")
        self.assertEqual(status, 200)
        self.assertEqual(payload["profile"]["version"], "abc123def456")
        self.assertEqual((payload["new_samples"], payload["ai_ready"], payload["ai_reason"]), (7, True, None))

    def test_save_rejects_invalid_bodies(self):
        self.assertEqual(self.request("POST", "/api/taste_profile/save", {"profile": {"summary": ""}})[0], 400)
        self.assertEqual(self.request("POST", "/api/taste_profile/save", {"profile": "text"})[0], 400)
        self.assertEqual(self.request("POST", "/api/taste_profile/save", [1])[0], 400)
        self.assertEqual(self.request("POST", "/api/taste_profile/save", raw="{bad")[0], 400)
        self.assertNotIn("profile", self.store)

    def test_generate_profile_job(self):
        result = {"status": "ok", "message": "done", "profile": {"version": "v1"}, "failed": 0, "errors": []}
        with patch.object(get_RSS, "generate_taste_profile", create=True, return_value=result) as generate:
            status, payload = self.request("POST", "/api/taste_profile", {})
            self.assertEqual(status, 202)
            self.assertFalse(payload["duplicate"])
            self.assertEqual(payload["job"]["kind"], "taste_profile")
            job = self.wait_job(payload["job"]["id"])
        generate.assert_called_once_with()
        self.assertEqual((job["status"], job["result"]), ("succeeded", result))

    def test_generate_profile_job_without_body_and_skipped(self):
        result = {"status": "skipped", "message": "样本不足", "profile": None, "failed": 0, "errors": []}
        with patch.object(get_RSS, "generate_taste_profile", create=True, return_value=result):
            status, payload = self.request("POST", "/api/taste_profile")
            self.assertEqual(status, 202)
            job = self.wait_job(payload["job"]["id"])
        self.assertEqual(job["result"]["status"], "skipped")
        self.assertEqual(self.request("POST", "/api/taste_profile", [1])[0], 400)

    def test_score_job_passes_rescore_and_reports_partial_failure(self):
        result = {"status": "partial_failed", "message": "m", "scored": 1, "failed": 1, "skipped": 0,
                  "errors": ["x"]}
        with patch.object(get_RSS, "score_inbox_with_taste", create=True, return_value=result) as score:
            status, payload = self.request("POST", "/api/taste_score", {"rescore": True})
            self.assertEqual(status, 202)
            self.assertEqual(payload["job"]["kind"], "taste_score")
            job = self.wait_job(payload["job"]["id"])
            score.assert_called_once_with(rescore=True)
            self.assertEqual(job["status"], "partial_failed")

            status, payload = self.request("POST", "/api/taste_score")
            self.assertEqual(status, 202)
            self.wait_job(payload["job"]["id"])
            self.assertEqual(score.call_args.kwargs, {"rescore": False})

    def test_score_rejects_non_boolean_rescore(self):
        self.assertEqual(self.request("POST", "/api/taste_score", {"rescore": "yes"})[0], 400)
        self.assertEqual(self.request("POST", "/api/taste_score", [True])[0], 400)

    def test_duplicate_score_job_is_reused(self):
        release = threading.Event()

        def slow(rescore=False):
            release.wait(timeout=3)
            return {"status": "ok", "scored": 0, "failed": 0, "skipped": 0, "errors": []}

        with patch.object(get_RSS, "score_inbox_with_taste", create=True, side_effect=slow):
            first = self.request("POST", "/api/taste_score", {})[1]
            second = self.request("POST", "/api/taste_score", {"rescore": True})[1]
            self.assertTrue(second["duplicate"])
            self.assertEqual(first["job"]["id"], second["job"]["id"])
            release.set()
            self.wait_job(first["job"]["id"])

    def test_pending_counts(self):
        with patch.object(get_RSS, "pending_taste_items", create=True, return_value=[]) as pending:
            status, payload = self.request("GET", "/api/taste_score/pending")
            self.assertEqual(status, 200)
            self.assertEqual(payload, {"pending": 0, "total_inbox": 2, "profile_version": None})
            pending.assert_not_called()

            self.store["profile"] = {"version": "v42", "summary": "s"}
            pending.return_value = [{"paper_id": self.inbox_ids[0]}]
            status, payload = self.request("GET", "/api/taste_score/pending")
            self.assertEqual(payload, {"pending": 1, "total_inbox": 2, "profile_version": "v42"})
            pending.assert_called_once_with(self.database)

    def test_cross_site_post_is_rejected(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        conn.request("POST", "/api/taste_score", "{}", {"Content-Type": "application/json",
                                                        "Origin": "https://evil.example"})
        response = conn.getresponse()
        response.read()
        conn.close()
        self.assertEqual(response.status, 403)


if __name__ == "__main__":
    unittest.main()
