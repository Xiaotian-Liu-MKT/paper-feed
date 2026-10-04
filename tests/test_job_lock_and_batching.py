"""Cross-process job lock, batched AI summaries and the abstract lookup job/endpoints."""
import datetime
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

import get_RSS
import server
from ai_test_guard import setUpModule, tearDownModule  # noqa: E402,F401 (no real Codex CLI)
from paper_feed import cli, locks
from paper_feed.db import connect
from paper_feed.exporter import database_items
from paper_feed.ingestion import ingest_fetch_results
from test_server_security import ServerTestCase

LONG = "This abstract is long enough to count as a real abstract. " * 4


def write_holder(path, pid, started_at=None, host=None, kind="refresh"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump({"pid": pid, "kind": kind, "host": host or socket.gethostname(),
                   "started_at": (started_at or datetime.datetime.now()).isoformat(timespec="seconds")}, handle)


def foreign_live_pid():
    """A process that is alive and is not this one (our parent)."""
    return os.getppid()


class LockTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.temp.name, "data", "feed.sqlite3")
        self.env = patch.dict(os.environ, {"PAPER_FEED_DB": self.db})
        self.env.start()
        self.path = str(locks.lock_path())

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()


class JobLockTests(LockTestCase):
    def test_lock_path_follows_database_dir(self):
        self.assertEqual(self.path, os.path.join(os.path.dirname(os.path.abspath(self.db)), ".paper_feed.lock"))
        with patch.dict(os.environ, {"PAPER_FEED_DB": ""}):
            self.assertEqual(locks.lock_path(), locks.PROJECT_DIR / "data" / ".paper_feed.lock")

    def test_lock_file_written_and_removed(self):
        with locks.job_lock("refresh"):
            holder = locks.read_holder(self.path)
            self.assertEqual((holder["pid"], holder["kind"], holder["host"]),
                             (os.getpid(), "refresh", socket.gethostname()))
            self.assertIn("started_at", holder)
        self.assertFalse(os.path.exists(self.path))

    def test_busy_when_another_live_process_holds_it(self):
        write_holder(self.path, foreign_live_pid(), kind="summarize")
        with self.assertRaises(locks.LockBusyError) as caught:
            with locks.job_lock("refresh"):
                self.fail("must not enter")
        self.assertEqual(caught.exception.holder["kind"], "summarize")
        self.assertIn("另一个任务正在运行", str(caught.exception))
        self.assertIn("Another Paper Feed task is running", str(caught.exception))
        self.assertTrue(os.path.exists(self.path))  # the holder's lock is untouched

    def test_dead_pid_lock_is_reclaimed(self):
        child = subprocess.Popen([sys.executable, "-c", "pass"])
        child.wait()
        self.assertFalse(locks.pid_alive(child.pid))
        self.assertTrue(locks.pid_alive(os.getpid()))
        write_holder(self.path, child.pid)
        with redirect_stdout(io.StringIO()), locks.job_lock("refresh"):
            self.assertEqual(locks.read_holder(self.path)["pid"], os.getpid())
        self.assertFalse(os.path.exists(self.path))

    def test_old_lock_is_reclaimed_even_if_pid_alive(self):
        old = datetime.datetime.now() - datetime.timedelta(seconds=locks.STALE_AFTER_SECONDS + 60)
        write_holder(self.path, foreign_live_pid(), started_at=old)
        with redirect_stdout(io.StringIO()), locks.job_lock("reanalyze"):
            self.assertEqual(locks.read_holder(self.path)["kind"], "reanalyze")

    def test_other_host_is_never_considered_dead(self):
        write_holder(self.path, 999999, host="another-machine")
        with self.assertRaises(locks.LockBusyError):
            with locks.job_lock("refresh"):
                pass

    def test_reentrant_in_thread_busy_for_other_threads(self):
        errors = []

        def other_thread():
            try:
                with locks.job_lock("summarize"):
                    errors.append("entered")
            except locks.LockBusyError as error:
                errors.append(error)

        with locks.job_lock("refresh"):
            with locks.job_lock("fetch_abstracts"):  # nested: no deadlock
                self.assertEqual(locks.read_holder(self.path)["kind"], "refresh")
            self.assertTrue(os.path.exists(self.path))  # still held by the outer level
            worker = threading.Thread(target=other_thread)
            worker.start(); worker.join(timeout=5)
        self.assertIsInstance(errors[0], locks.LockBusyError)
        self.assertFalse(os.path.exists(self.path))
        with locks.job_lock("again"):  # released completely
            pass

    def test_nested_flows_do_not_deadlock(self):
        ingest_fetch_results([{"url": "s", "success": True, "entries": [
            {"id": "g1", "title": "T", "link": "https://x.test/1", "journal": "J", "summary": ""}]}],
            self.temp.name, self.db)
        with patch.object(get_RSS, "OUTPUT_FILE", os.path.join(self.temp.name, "out.xml")), \
                patch.object(get_RSS, "FEED_JSON", os.path.join(self.temp.name, "feed.json")), \
                patch.object(get_RSS, "get_config", return_value={}), \
                redirect_stdout(io.StringIO()):
            with locks.job_lock("summarize"):
                result = get_RSS.fetch_missing_abstracts(view="all")
        self.assertEqual(result["status"], "ok")
        self.assertFalse(os.path.exists(self.path))


class LockedFlowEntryPointTests(LockTestCase):
    def test_cli_refresh_exits_1_when_busy(self):
        write_holder(self.path, foreign_live_pid(), kind="summarize")
        out = io.StringIO()
        with patch.object(get_RSS, "load_config", side_effect=AssertionError("must not run")), redirect_stdout(out):
            self.assertEqual(cli.main(["refresh"]), 1)
        self.assertIn("Another Paper Feed task is running (summarize", out.getvalue())
        self.assertIn("另一个任务正在运行", out.getvalue())

    def test_cli_fetch_abstracts_exits_1_when_busy(self):
        ingest_fetch_results([], self.temp.name, self.db)
        write_holder(self.path, foreign_live_pid(), kind="refresh")
        out = io.StringIO()
        with patch.object(cli, "_database_path", return_value=self.db), \
                patch("paper_feed.ingestion.paper_ids_in_view", return_value=["x"]), redirect_stdout(out):
            self.assertEqual(cli.main(["fetch-abstracts", "--yes"]), 1)
        self.assertIn("Another Paper Feed task is running (refresh", out.getvalue())

    def test_server_job_fails_with_bilingual_message(self):
        write_holder(self.path, foreign_live_pid(), kind="refresh")
        runner = server.JobRunner()
        job, _ = runner.enqueue("fetch_abstracts", server.make_fetch_abstracts_job("favorite"))
        for _ in range(200):
            job = runner.get(job["id"])
            if job["status"] not in {"queued", "running"}:
                break
            time.sleep(0.01)
        self.assertEqual(job["status"], "failed")
        self.assertIn("另一个任务正在运行（refresh", job["message"])
        self.assertIn("Another Paper Feed task is running", job["message"])
        self.assertEqual(job["result"]["error"], "lock_busy")
        self.assertEqual(job["result"]["holder"]["kind"], "refresh")


def codex_settings():
    return get_RSS.CodexCLISettings("codex-test-executable")


class FakeClient:
    """Records prompts; *reply(prompt)* returns the JSON text of a response."""

    def __init__(self, reply):
        self.reply = reply
        self.prompts = []
        self.timeouts = []
        self.chat = MagicMock()
        self.chat.completions.create.side_effect = self._create

    def with_options(self, timeout=None, **ignored):
        self.timeouts.append(timeout)
        return self

    def _create(self, **kwargs):
        prompt = kwargs["messages"][-1]["content"]
        self.prompts.append(prompt)
        message = MagicMock(content=self.reply(prompt))
        return MagicMock(choices=[MagicMock(message=message)])


def paper_count(prompt):
    return prompt.count("] MODE: ")


class BatchSummaryTests(unittest.TestCase):
    def test_batch_aligns_by_index_and_leaves_missing_pending(self):
        entries = [{"title": "A", "journal": "J", "raw": LONG}, {"title": "B", "journal": "J", "raw": None},
                   {"title": "C", "journal": "J", "raw": "short raw"}]
        reply = json.dumps({"results": [{"index": 2, "summary": "可能研究B"}, {"index": 1, "summary": "<b>A总结</b>"}]})
        client = FakeClient(lambda prompt: reply)
        with patch.object(get_RSS, "make_openai_client", return_value=client):
            outcomes = get_RSS.summarize_batch_with_gpt(entries, codex_settings())
        self.assertEqual(outcomes[0], ("A总结", None))
        self.assertEqual(outcomes[1], (get_RSS.TITLE_ONLY_PREFIX + "可能研究B", None))
        self.assertIsNone(outcomes[2][0]); self.assertIn("pending", outcomes[2][1])
        prompt = client.prompts[0]
        self.assertIn("[1] MODE: ABSTRACT", prompt); self.assertIn("[2] MODE: TITLE-ONLY", prompt)
        self.assertIn(get_RSS.TITLE_ONLY_PREFIX, prompt); self.assertIn("研究主题、可能的研究方法、主要贡献", prompt)
        self.assertEqual(client.timeouts, [get_RSS.CODEX_TIMEOUT_SECONDS + 3 * get_RSS.CODEX_SUMMARY_SECONDS_PER_ITEM])

    def test_batch_error_fails_every_item(self):
        client = FakeClient(lambda prompt: "not json")
        with patch.object(get_RSS, "make_openai_client", return_value=client):
            outcomes = get_RSS.summarize_batch_with_gpt([{"title": "A", "raw": LONG}, {"title": "B"}], codex_settings())
        self.assertTrue(all(summary is None and error for summary, error in outcomes))

    def test_openai_keeps_per_paper_calls(self):
        with patch.object(get_RSS, "summarize_abstract_with_gpt", return_value="S") as summarize, \
                patch.object(get_RSS, "generate_abstract_with_gpt", return_value=None) as generate, \
                patch.object(get_RSS, "summarize_batch_with_gpt") as batch:
            outcomes = get_RSS.generate_summaries([{"title": "A", "raw": LONG}, {"title": "B", "raw": None}], "key")
        batch.assert_not_called()
        self.assertEqual((summarize.call_count, generate.call_count), (1, 1))
        self.assertEqual(outcomes, [("S", None), (None, "empty response")])


class BatchedSummarizeFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db = os.path.join(self.temp.name, "data", "feed.sqlite3")
        entries = [{"id": f"guid-{n}", "title": f"Paper {n}", "link": f"https://x.test/{n}", "journal": "J",
                    "summary": "", "pub_date": datetime.datetime(2024, 1, n + 1, tzinfo=datetime.timezone.utc)}
                   for n in range(10)]
        ingest_fetch_results([{"url": "s", "success": True, "entries": entries}], self.temp.name, self.db)
        self.ids = {item["id"]: item["paper_id"] for item in database_items(self.db)}
        # Paper 0 already has a user-provided raw abstract -> gpt_summarized.
        from paper_feed.service import PaperFeedService
        PaperFeedService(self.temp.name, self.db, import_legacy=False).save_abstract(self.ids["guid-0"], LONG)
        self.patches = [
            patch.dict(os.environ, {"PAPER_FEED_DB": self.db}),
            patch.object(get_RSS, "OUTPUT_FILE", os.path.join(self.temp.name, "out.xml")),
            patch.object(get_RSS, "FEED_JSON", os.path.join(self.temp.name, "web", "feed.json")),
            patch.object(get_RSS, "load_config", return_value=["paper"]),
            patch.object(get_RSS, "get_config", return_value={}),
            patch.object(get_RSS, "find_codex_executable", return_value="codex-test-executable"),
            patch.object(get_RSS, "fetch_abstract_with_fallback", return_value=(None, None, None)),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def test_codex_summaries_are_batched_and_unaligned_items_stay_pending(self):
        def reply(prompt):
            count = paper_count(prompt)
            # The model drops "Paper 9" (title-only), everything else is answered out of order.
            results = []
            for index in range(count, 0, -1):
                if f"[{index}] MODE: TITLE-ONLY\nTitle: Paper 9" in prompt:
                    continue
                results.append({"index": index, "summary": f"摘要{index}"})
            return json.dumps({"results": results})

        client = FakeClient(reply)
        with patch.object(get_RSS, "make_openai_client", return_value=client), redirect_stdout(io.StringIO()):
            result = get_RSS.summarize_specific_papers(list(self.ids))
        self.assertEqual([paper_count(prompt) for prompt in client.prompts], [8, 2])
        self.assertEqual((result["updated"], result["failed"]), (9, 1))
        self.assertIn("Paper 9", result["errors"][0])
        items = {item["id"]: item for item in database_items(self.db)}
        first = items["guid-0"]["abstract"]
        self.assertEqual((first["source"], first["raw_abstract"], first["raw_source"]),
                         ("gpt_summarized", LONG, "user_provided"))
        guess = items["guid-1"]["abstract"]
        self.assertEqual(guess["source"], "gpt_generated")
        self.assertTrue(guess["abstract"].startswith(get_RSS.TITLE_ONLY_PREFIX))
        self.assertEqual([item["title"] for item in get_RSS.pending_summary_items(self.db, list(self.ids))],
                         ["Paper 9"])


class FetchAbstractEndpointTests(ServerTestCase):
    def test_pending_counts(self):
        status, payload = self.request("GET", "/api/fetch_abstracts/pending?view=all")
        self.assertEqual((status, payload), (200, {"view": "all", "total": 2, "pending": 2, "with_doi": 1}))
        status, payload = self.request("GET", "/api/fetch_abstracts/pending")
        self.assertEqual(payload, {"view": "favorite", "total": 0, "pending": 0, "with_doi": 0})
        self.assertEqual(self.request("GET", "/api/fetch_abstracts/pending?view=bogus")[0], 400)

    def test_post_enqueues_fetch_abstracts_job(self):
        with patch.object(server.JOB_RUNNER, "enqueue", return_value=({"id": "x", "kind": "fetch_abstracts"}, False)) as enqueue:
            status, payload = self.request("POST", "/api/fetch_abstracts", {"view": "all"})
            self.assertEqual((status, payload["job"]["id"], payload["duplicate"], payload["view"]), (202, "x", False, "all"))
            self.assertEqual(enqueue.call_args.args[0], "fetch_abstracts")
            self.assertEqual(self.request("POST", "/api/fetch_abstracts")[0], 202)  # body-less -> favorite
            self.assertEqual(self.request("POST", "/api/fetch_abstracts", {"view": "hidden"})[0], 400)
            self.assertEqual(self.request("POST", "/api/fetch_abstracts", {"view": "all"},
                                          headers={"Origin": "http://evil.example"})[0], 403)

    def test_job_runs_lookup_and_reports_counts(self):
        with patch.object(get_RSS, "fetch_abstract_with_fallback",
                          side_effect=lambda entry, **kw: (LONG, "crossref", LONG) if entry.get("doi") else (None, None, None)), \
                patch.object(get_RSS, "OUTPUT_FILE", os.path.join(self.root, "out.xml")), \
                patch.object(get_RSS, "FEED_JSON", os.path.join(self.root, "feed.json")), \
                redirect_stdout(io.StringIO()):
            result = server.make_fetch_abstracts_job("all")()
        self.assertEqual((result["fetched"], result["failed"], result["skipped"], result["errors"]), (1, 0, 1, []))
        status, payload = self.request("GET", "/api/fetch_abstracts/pending?view=all")
        self.assertEqual((payload["pending"], payload["with_doi"]), (1, 0))


if __name__ == "__main__":
    unittest.main()
