"""AI taste profile storage, generation and inbox scoring (offline: the AI client is faked)."""
import datetime
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import get_RSS  # noqa: E402
from ai_test_guard import setUpModule, tearDownModule  # noqa: E402,F401 (no real Codex CLI)
from paper_feed import taste  # noqa: E402
from paper_feed.db import connect, database_counts  # noqa: E402
from paper_feed.exporter import database_items, export_items  # noqa: E402
from paper_feed.ingestion import ingest_fetch_results, save_abstracts, save_translations  # noqa: E402
from paper_feed.service import PaperFeedService  # noqa: E402

PROFILE = {"summary": "偏好消费者心理机制研究", "likes": ["消费者对 AI 的心理反应"], "dislikes": ["宏观经济政策"],
           "boundaries": ["同样研究 AI，要消费者视角，不要企业采纳"], "methods": ["实验"]}


class FakeClient:
    """Records prompts; *reply(prompt)* returns the text of a response."""

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


def make_store(directory, count=20):
    db = os.path.join(directory, "data", "feed.sqlite3")
    entries = [{"id": f"guid-{n}", "title": f"Paper {n}", "link": f"https://x.test/{n}", "journal": "J",
                "summary": "", "pub_date": datetime.datetime(2024, 1, 1, tzinfo=datetime.timezone.utc) + datetime.timedelta(days=n)}
               for n in range(count)]
    with redirect_stdout(io.StringIO()):
        ingest_fetch_results([{"url": "s", "success": True, "entries": entries}], directory, db)
    ids = {item["id"]: item["paper_id"] for item in database_items(db)}
    return db, ids


def review(directory, db, paper_ids, action):
    service = PaperFeedService(directory, db, import_legacy=False)
    for paper_id in paper_ids:
        service.review(paper_id, action)


class TasteStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db, self.ids = make_store(self.temp.name, 6)

    def tearDown(self):
        self.temp.cleanup()

    def test_save_profile_normalizes_versions_and_keeps_history(self):
        self.assertIsNone(taste.load_profile(self.db))
        raw = {"summary": "  偏好 ", "likes": ["  a ", "", "a", None, "- b"] + [f"x{n}" for n in range(20)],
               "dislikes": "c\n\n d ", "boundaries": None, "methods": ["m"], "extra": "ignored"}
        saved = taste.save_profile(self.db, raw, "ai", model="m1", sample_counts={"favorite": 3, "archived": 1})
        self.assertEqual(saved["summary"], "偏好")
        self.assertEqual(saved["likes"][:2], ["a", "b"])
        self.assertEqual(len(saved["likes"]), taste.TASTE_MAX_ITEMS)
        self.assertEqual(saved["dislikes"], ["c", "d"])
        self.assertEqual(saved["boundaries"], [])
        self.assertNotIn("extra", saved)
        self.assertEqual(saved["sample_counts"], {"favorite": 3, "archived": 1, "hidden": 0})
        self.assertEqual((saved["source"], saved["model"]), ("ai", "m1"))
        self.assertRegex(saved["version"], r"^[0-9a-f]{12}$")
        self.assertEqual(taste.load_profile(self.db), saved)

        user = taste.save_profile(self.db, PROFILE, "user")
        self.assertNotEqual(user["version"], saved["version"])
        self.assertEqual(taste.load_profile(self.db)["source"], "user")
        self.assertEqual(user["sample_counts"], {"favorite": 0, "archived": 0, "hidden": 0})
        conn = connect(self.db)
        try:
            self.assertEqual(database_counts(conn)["taste_profiles"], 2)
        finally:
            conn.close()
        # Identical content re-activates the same version instead of violating UNIQUE.
        again = taste.save_profile(self.db, raw, "user")
        self.assertEqual(again["version"], saved["version"])
        self.assertEqual(taste.load_profile(self.db)["version"], saved["version"])
        self.assertEqual(taste.profile_version(taste.normalize_profile(raw)), saved["version"])

    def test_save_profile_rejects_invalid_input(self):
        for bad in (None, [], "text", {}, {"summary": "  ", "likes": []}, {"likes": "x", "summary": 3},
                    {"likes": [{"a": 1}]}, {"likes": 5}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                taste.save_profile(self.db, bad, "user")
        with self.assertRaises(ValueError):
            taste.save_profile(self.db, PROFILE, "robot")
        self.assertIsNone(taste.load_profile(self.db))

    def test_counts_samples_since_profile_and_thresholds(self):
        ids = list(self.ids.values())
        review(self.temp.name, self.db, ids[:2], "like")
        review(self.temp.name, self.db, ids[2:3], "archive")
        review(self.temp.name, self.db, ids[3:4], "hide")
        self.assertEqual(taste.sample_counts(self.db), {"favorite": 2, "archived": 1, "hidden": 1})
        self.assertEqual(taste.samples_since_profile(self.db, None), 4)
        profile = taste.save_profile(self.db, PROFILE, "user")
        self.assertEqual(taste.samples_since_profile(self.db, profile), 0)
        review(self.temp.name, self.db, ids[4:5], "hide")
        review(self.temp.name, self.db, ids[0:1], "unlike")  # not a sample event
        self.assertEqual(taste.samples_since_profile(self.db, profile), 1)
        self.assertFalse(taste.has_enough_samples({"favorite": 4, "archived": 0, "hidden": 20}))
        self.assertFalse(taste.has_enough_samples({"favorite": 3, "archived": 3, "hidden": 3}))
        self.assertTrue(taste.has_enough_samples({"favorite": 3, "archived": 2, "hidden": 5}))

    def test_save_scores_upserts_and_service_exposes_but_exporter_does_not(self):
        paper_id = self.ids["guid-0"]
        saved = taste.save_scores(self.db, {paper_id: {"score": 182.4, "reason": " 很 <b>契合</b> "},
                                            self.ids["guid-1"]: {"score": "n/a"}, "missing": {"score": 5}}, "v1")
        self.assertEqual(saved, 1)
        self.assertEqual(taste.save_scores(self.db, {paper_id: {"score": "64", "reason": "部分匹配"}}, "v2"), 1)
        service = PaperFeedService(self.temp.name, self.db, import_legacy=False)
        record = service.get_paper(paper_id)
        self.assertEqual((record["taste_score"], record["taste_reason"], record["taste_profile_version"]),
                         (64, "部分匹配", "v2"))
        other = service.get_paper(self.ids["guid-1"])
        self.assertEqual((other["taste_score"], other["taste_reason"], other["taste_profile_version"]), (None, "", ""))
        inbox = {item["paper_id"]: item for item in service.list_papers("inbox")}
        self.assertEqual(inbox[paper_id]["taste_score"], 64)
        xml = os.path.join(self.temp.name, "out.xml")
        feed = os.path.join(self.temp.name, "feed.json")
        export_items(database_items(self.db), xml, feed)
        with open(feed, encoding="utf-8") as handle:
            self.assertNotIn("taste", handle.read())

    def test_stale_items_and_collect_samples(self):
        ids = self.ids
        save_translations(self.db, {ids["guid-0"]: {"zh": "零", "methods": [{"name": "Experiment"}],
                                                    "topics": [{"name": "AI & Tech"}]}})
        save_abstracts(self.db, {ids["guid-0"]: {"abstract": "A" * 50, "raw_abstract": "Raw text", "source": "crossref"},
                                 ids["guid-1"]: {"abstract": "AI guess", "source": "gpt_generated"}})
        review(self.temp.name, self.db, [ids["guid-0"], ids["guid-1"]], "like")
        review(self.temp.name, self.db, [ids["guid-2"]], "hide")
        samples = taste.collect_samples(self.db, limit=1)
        self.assertEqual(len(samples["positive"]), 1)
        both = {item["paper_id"]: item for item in taste.collect_samples(self.db)["positive"]}
        self.assertEqual(both[ids["guid-0"]]["methods"], ["Experiment"])
        self.assertEqual(both[ids["guid-0"]]["topics"], ["AI & Tech"])
        self.assertEqual(both[ids["guid-0"]]["raw_abstract"], "Raw text")
        self.assertEqual(both[ids["guid-1"]]["raw_abstract"], "")  # an AI guess is not evidence
        self.assertEqual([item["paper_id"] for item in samples["hidden"]], [ids["guid-2"]])

        profile = {"version": "v2"}
        taste.save_scores(self.db, {ids["guid-3"]: {"score": 50}, ids["guid-4"]: {"score": 70}}, "v1")
        taste.save_scores(self.db, {ids["guid-4"]: {"score": 70}}, "v2")
        inbox = taste.inbox_items(self.db)
        self.assertEqual({item["paper_id"] for item in inbox}, {ids["guid-3"], ids["guid-4"], ids["guid-5"]})
        stale = {item["paper_id"] for item in get_RSS.stale_taste_items(inbox, profile)}
        self.assertEqual(stale, {ids["guid-3"], ids["guid-5"]})
        self.assertEqual(get_RSS.stale_taste_items(inbox, None), [])
        # Service records (taste_score / taste_profile_version) work too; other states are ignored.
        records = PaperFeedService(self.temp.name, self.db, import_legacy=False).list_papers("all")
        stale = {item["paper_id"] for item in get_RSS.stale_taste_items(records, profile)}
        self.assertEqual(stale, {ids["guid-3"], ids["guid-5"]})
        self.assertEqual(get_RSS.pending_taste_items(self.db), [])
        taste.save_profile(self.db, PROFILE, "user")
        self.assertEqual(len(get_RSS.pending_taste_items(self.db)), 3)


class TasteFlowTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db, self.ids = make_store(self.temp.name, 40)
        ids = list(self.ids.values())
        review(self.temp.name, self.db, ids[:4], "like")
        review(self.temp.name, self.db, ids[4:6], "archive")
        review(self.temp.name, self.db, ids[6:10], "hide")
        save_abstracts(self.db, {ids[0]: {"abstract": "Long raw abstract " * 40, "raw_abstract": "Long raw abstract " * 40,
                                          "source": "user_provided"}})
        self.patches = [patch.dict(os.environ, {"PAPER_FEED_DB": self.db}),
                        patch.object(get_RSS, "get_config", return_value={"OPENAI_API_KEY": "sk-test",
                                                                          "AI_BACKEND": "openai"})]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def run_quiet(self, func, *args, **kwargs):
        with redirect_stdout(io.StringIO()):
            return func(*args, **kwargs)


class GenerateProfileTests(TasteFlowTestCase):
    def test_generates_and_stores_profile_from_samples(self):
        reply = json.dumps({"profile": dict(PROFILE, likes=["消费者对 AI 的心理反应", "<b>情绪</b>"])})
        client = FakeClient(lambda prompt: reply)
        with patch.object(get_RSS, "make_openai_client", return_value=client):
            result = self.run_quiet(get_RSS.generate_taste_profile)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual((result["failed"], result["errors"]), (0, []))
        profile = taste.load_profile(self.db)
        self.assertEqual(result["profile"], profile)
        self.assertEqual((profile["source"], profile["model"]), ("ai", "gpt-4o-mini"))
        self.assertEqual(profile["sample_counts"], {"favorite": 4, "archived": 2, "hidden": 4})
        self.assertEqual(len(client.prompts), 1)
        prompt = client.prompts[0]
        self.assertIn("FAVORITE (4)", prompt); self.assertIn("ARCHIVED (2)", prompt); self.assertIn("HIDDEN (4)", prompt)
        self.assertIn("theoretical lens", prompt); self.assertIn("boundary judgment", prompt)
        self.assertIn("Simplified Chinese", prompt)
        # Positive abstracts are truncated to ~300 characters.
        self.assertIn("Long raw abstract", prompt)
        self.assertNotIn("Long raw abstract " * 20, prompt)
        self.assertEqual(client.chat.completions.create.call_args.kwargs["response_format"], {"type": "json_object"})

    def test_sample_limit_uses_most_recent_reviews(self):
        with patch.object(get_RSS, "TASTE_SAMPLE_LIMIT", 2):
            client = FakeClient(lambda prompt: json.dumps(PROFILE))
            with patch.object(get_RSS, "make_openai_client", return_value=client):
                self.run_quiet(get_RSS.generate_taste_profile)
        prompt = client.prompts[0]
        self.assertEqual(prompt.count("\n[H"), 2)
        self.assertEqual(prompt.count("\n[F") + prompt.count("\n[A"), 2)

    def test_skips_without_samples_or_ai(self):
        client = FakeClient(lambda prompt: json.dumps(PROFILE))
        with patch.object(get_RSS, "make_openai_client", return_value=client):
            with patch.object(get_RSS, "get_config", return_value={}):
                result = self.run_quiet(get_RSS.generate_taste_profile)
            self.assertEqual(result["status"], "skipped")
            self.assertIn("No AI backend", result["message"])
            review(self.temp.name, self.db, list(self.ids.values())[:3], "unlike")
            result = self.run_quiet(get_RSS.generate_taste_profile)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("Not enough samples", result["message"])
        self.assertEqual(client.prompts, [])
        self.assertIsNone(taste.load_profile(self.db))

    def test_bad_reply_is_an_error_and_nothing_is_saved(self):
        for reply in ("not json", json.dumps({"summary": "", "likes": []})):
            client = FakeClient(lambda prompt: reply)
            with self.subTest(reply=reply), patch.object(get_RSS, "make_openai_client", return_value=client):
                result = self.run_quiet(get_RSS.generate_taste_profile)
            self.assertEqual((result["status"], result["failed"], result["profile"]), ("error", 1, None))
            self.assertTrue(result["errors"])
        self.assertIsNone(taste.load_profile(self.db))

    def test_codex_backend_gets_longer_timeout(self):
        client = FakeClient(lambda prompt: json.dumps(PROFILE))
        with patch.object(get_RSS, "get_config", return_value={}), \
                patch.object(get_RSS, "find_codex_executable", return_value="codex-test-executable"), \
                patch.object(get_RSS, "make_openai_client", return_value=client):
            result = self.run_quiet(get_RSS.generate_taste_profile)
        self.assertEqual(result["status"], "ok")
        self.assertEqual(client.timeouts, [get_RSS.CODEX_TIMEOUT_SECONDS + get_RSS.TASTE_PROFILE_EXTRA_SECONDS])
        self.assertEqual(result["profile"]["model"], get_RSS.DEFAULT_CODEX_MODEL)

    def test_codex_subprocess_is_mocked_end_to_end(self):
        def fake_run(command, **kwargs):
            output = command[command.index("-o") + 1]
            with open(output, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(PROFILE, ensure_ascii=False))
            return MagicMock(returncode=0, stderr=b"")

        with patch.object(get_RSS, "get_config", return_value={}), \
                patch.object(get_RSS, "find_codex_executable", return_value="codex-test-executable"), \
                patch("subprocess.run", side_effect=fake_run) as run:
            result = self.run_quiet(get_RSS.generate_taste_profile)
        self.assertEqual(result["status"], "ok", result)
        run.assert_called_once()
        self.assertIn("边界判断", run.call_args.kwargs["input"].decode("utf-8"))


def score_reply(prompt, skip_title=None, score=80):
    count = prompt.count("] Title: ")
    results = []
    for index in range(count, 0, -1):
        if skip_title and f"[{index}] Title: {skip_title}\n" in prompt:
            continue
        results.append({"index": index, "score": score, "reason": f"<i>理由{index}</i>"})
    return json.dumps({"results": results})


class ScoreInboxTests(TasteFlowTestCase):
    def setUp(self):
        super().setUp()
        self.profile = taste.save_profile(self.db, PROFILE, "user")

    def test_scores_stale_inbox_in_batches_and_reports_unaligned(self):
        client = FakeClient(lambda prompt: score_reply(prompt, skip_title="Paper 0"))
        with patch.object(get_RSS, "make_openai_client", return_value=client):
            result = self.run_quiet(get_RSS.score_inbox_with_taste)
        self.assertEqual([prompt.count("] Title: ") for prompt in client.prompts], [10, 10, 10])
        self.assertEqual((result["status"], result["scored"], result["failed"], result["skipped"]),
                         ("partial_failed", 29, 1, 0))
        self.assertIn("Paper 0", result["errors"][0])
        self.assertIn("同样研究 AI", client.prompts[0])
        self.assertIn('"results"', client.prompts[0])
        record = PaperFeedService(self.temp.name, self.db, import_legacy=False).get_paper(self.ids["guid-10"])
        self.assertEqual(record["taste_score"], 80)
        self.assertNotIn("<", record["taste_reason"])
        self.assertEqual(record["taste_profile_version"], self.profile["version"])
        self.assertEqual([item["title"] for item in get_RSS.pending_taste_items(self.db)], ["Paper 0"])

        client = FakeClient(lambda prompt: score_reply(prompt))
        with patch.object(get_RSS, "make_openai_client", return_value=client):
            result = self.run_quiet(get_RSS.score_inbox_with_taste)
        self.assertEqual((result["status"], result["scored"], result["skipped"]), ("ok", 1, 29))
        with patch.object(get_RSS, "make_openai_client", return_value=client):
            result = self.run_quiet(get_RSS.score_inbox_with_taste)
        self.assertEqual((result["status"], result["scored"]), ("ok", 0))
        with patch.object(get_RSS, "make_openai_client", return_value=client):
            result = self.run_quiet(get_RSS.score_inbox_with_taste, rescore=True)
        self.assertEqual(result["scored"], 30)

    def test_new_profile_makes_scores_stale(self):
        client = FakeClient(lambda prompt: score_reply(prompt))
        with patch.object(get_RSS, "make_openai_client", return_value=client):
            self.run_quiet(get_RSS.score_inbox_with_taste)
        self.assertEqual(get_RSS.pending_taste_items(self.db), [])
        taste.save_profile(self.db, dict(PROFILE, summary="新画像"), "user")
        self.assertEqual(len(get_RSS.pending_taste_items(self.db)), 30)

    def test_codex_uses_large_chunks_and_errors_fail_every_item(self):
        client = FakeClient(lambda prompt: "not json")
        with patch.object(get_RSS, "get_config", return_value={}), \
                patch.object(get_RSS, "find_codex_executable", return_value="codex-test-executable"), \
                patch.object(get_RSS, "make_openai_client", return_value=client):
            result = self.run_quiet(get_RSS.score_inbox_with_taste)
        self.assertEqual(sorted(prompt.count("] Title: ") for prompt in client.prompts), [5, 25])
        self.assertEqual((result["status"], result["scored"], result["failed"]), ("error", 0, 30))

    def test_breaker_stops_after_repeated_codex_failures(self):
        calls = []

        def failing(*args, **kwargs):
            calls.append(1)
            raise get_RSS.CodexCLITimeoutError("timed out")

        client = MagicMock()
        client.with_options.return_value = client
        client.chat.completions.create.side_effect = failing
        with patch.object(get_RSS, "OPENAI_ANALYSIS_CHUNK_SIZE", 2), \
                patch.object(get_RSS, "AI_ANALYSIS_WORKERS", 1), \
                patch.object(get_RSS, "make_openai_client", return_value=client):
            result = self.run_quiet(get_RSS.score_inbox_with_taste)
        self.assertEqual(len(calls), get_RSS.OPENAI_BREAKER_THRESHOLD)
        self.assertEqual(result["failed"], 30)
        self.assertTrue(any("not responding" in error for error in result["errors"]))

    def test_skips_without_profile_or_ai(self):
        with patch.object(get_RSS, "get_config", return_value={}), \
                patch.object(get_RSS, "make_openai_client") as factory:
            result = self.run_quiet(get_RSS.score_inbox_with_taste)
        factory.assert_not_called()
        self.assertEqual((result["status"], result["skipped"]), ("skipped", 30))
        conn = connect(self.db)
        try:
            conn.execute("DELETE FROM taste_profiles")
            conn.commit()
        finally:
            conn.close()
        result = self.run_quiet(get_RSS.score_inbox_with_taste)
        self.assertEqual(result["status"], "skipped")
        self.assertIn("No taste profile", result["message"])


class RssFlowTasteHookTests(TasteFlowTestCase):
    def run_flow(self, client):
        fetched = {"url": "s", "success": True, "entries": [
            {"id": "guid-new", "title": "Paper new", "link": "https://x.test/new", "journal": "J", "summary": "",
             "pub_date": datetime.datetime(2025, 1, 1, tzinfo=datetime.timezone.utc)}]}
        with patch.object(get_RSS, "load_config", return_value=["paper"]), \
                patch.object(get_RSS, "fetch_rss_result", return_value=fetched), \
                patch.object(get_RSS, "JOURNAL_HASH_FILE", os.path.join(self.temp.name, "journals.hash")), \
                patch.object(get_RSS, "WEB_DIR", self.temp.name), \
                patch.object(get_RSS, "generate_rss_xml"), \
                patch.object(get_RSS, "analyze_database_items", return_value=0), \
                patch.object(get_RSS, "make_openai_client", return_value=client):
            return self.run_quiet(get_RSS.run_rss_flow)

    def test_refresh_scores_inbox_when_profile_exists(self):
        client = FakeClient(lambda prompt: score_reply(prompt))
        result = self.run_flow(client)
        self.assertTrue(result["published"])
        self.assertEqual(result["taste_scored"], 0)
        self.assertEqual(client.prompts, [])  # no profile: no AI call
        taste.save_profile(self.db, PROFILE, "user")
        result = self.run_flow(client)
        self.assertEqual(result["taste_scored"], 31)
        self.assertEqual(get_RSS.pending_taste_items(self.db), [])

    def test_refresh_survives_taste_failures(self):
        taste.save_profile(self.db, PROFILE, "user")
        result = self.run_flow(FakeClient(lambda prompt: "not json"))
        self.assertTrue(result["published"])
        self.assertEqual(result["ai_failed"], 31)
        self.assertTrue(any(error.startswith("Taste score") for error in result["errors"]))
        with patch.object(get_RSS, "_score_inbox_with_taste", side_effect=RuntimeError("boom")):
            result = self.run_flow(FakeClient(lambda prompt: "{}"))
        self.assertTrue(result["published"])
        self.assertTrue(any("boom" in error for error in result["errors"]))


if __name__ == "__main__":
    unittest.main()
