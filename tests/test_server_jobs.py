import threading
import time
import unittest

from server import JobRunner


class JobRunnerTests(unittest.TestCase):
    def test_duplicate_kind_reuses_queued_or_running_job(self):
        runner = JobRunner()
        started = threading.Event()
        release = threading.Event()

        def work():
            started.set()
            release.wait(timeout=2)
            return {"published": True, "successful_sources": ["source"], "failed_sources": []}

        first, duplicate = runner.enqueue("fetch", work)
        self.assertFalse(duplicate)
        self.assertTrue(started.wait(timeout=1))
        second, duplicate = runner.enqueue("fetch", work)
        self.assertTrue(duplicate)
        self.assertEqual(first["id"], second["id"])

        release.set()
        for _ in range(30):
            status = runner.get(first["id"])
            if status["status"] == "succeeded":
                break
            time.sleep(0.02)
        self.assertEqual(status["status"], "succeeded")
        self.assertEqual(status["progress"], 100)

    def test_partial_and_total_fetch_failures_are_reported(self):
        runner = JobRunner()
        partial, _ = runner.enqueue("fetch", lambda: {
            "published": True, "successful_sources": ["ok"], "failed_sources": ["bad"]
        })
        failed, _ = runner.enqueue("reanalyze", lambda: {"status": "error", "message": "No API Key"})

        for _ in range(30):
            partial_status = runner.get(partial["id"])
            failed_status = runner.get(failed["id"])
            if partial_status["status"] == "partial_failed" and failed_status["status"] == "failed":
                break
            time.sleep(0.02)
        self.assertEqual(partial_status["status"], "partial_failed")
        self.assertEqual(failed_status["status"], "failed")


if __name__ == "__main__":
    unittest.main()
