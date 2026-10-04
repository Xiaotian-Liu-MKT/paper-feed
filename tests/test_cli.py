"""Tests for the unified command line (`python -m paper_feed <command>`).

No test touches the network or OpenAI: refresh/serve/AI flows are mocked.
"""
import contextlib
import io
import json
import os
import socket
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import get_RSS  # noqa: E402
import server  # noqa: E402
from paper_feed import cli, publish_guard  # noqa: E402
from paper_feed.db import PaperRepository, connect  # noqa: E402
from paper_feed.ingestion import save_abstracts, save_translations  # noqa: E402

COMMANDS = ["start", "run", "serve", "refresh", "reanalyze", "summarize-favorites", "keywords",
            "fetch-abstracts", "doctor", "backup", "restore", "import-legacy", "publish-guard"]
CLEAN_ENV_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_PROXY", "OPENAI_MODEL", "RSS_KEYWORDS",
                  "RSS_JOURNALS", "PAPER_FEED_DB", "PAPER_FEED_PORT")


def run_cli(*argv):
    """Run cli.main and return (exit code, stdout + stderr)."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            code = cli.main(list(argv))
        except SystemExit as exit_:
            code = exit_.code
    return code, out.getvalue()


def clean_env(**extra):
    env = {key: value for key, value in os.environ.items() if key not in CLEAN_ENV_KEYS}
    env.update(extra)
    return patch.dict(os.environ, env, clear=True)


def make_database(path, titles=("Alpha paper", "Beta paper", "Gamma paper")):
    """Create a small SQLite store; returns {title: paper_id}."""
    conn = connect(str(path))
    ids = {}
    try:
        repo = PaperRepository(conn)
        with repo.transaction():
            for index, title in enumerate(titles):
                paper_id = repo.resolve({"title": title, "link": f"https://example.test/{index}"})
                repo.ensure_inbox(paper_id)
                repo.add_legacy_alias(paper_id, f"legacy-{index}")
                ids[title] = paper_id
    finally:
        conn.close()
    return ids


def set_state(path, paper_id, state):
    conn = connect(str(path))
    try:
        conn.execute("UPDATE paper_review_state SET state=? WHERE paper_id=?", (state, paper_id))
        conn.commit()
    finally:
        conn.close()


class HelpTests(unittest.TestCase):
    def test_bare_invocation_prints_help_instead_of_importing(self):
        with patch("paper_feed.importer.LegacyImporter.run") as importer:
            code, output = run_cli()
        self.assertEqual(code, 0)
        importer.assert_not_called()
        self.assertIn("usage: python -m paper_feed", output)
        for command in COMMANDS:
            self.assertIn(command, output)

    def test_every_subcommand_has_bilingual_help(self):
        for command in COMMANDS:
            with self.subTest(command=command):
                code, output = run_cli(command, "--help")
                self.assertEqual(code, 0)
                self.assertIn(f"usage: python -m paper_feed {command}", output)
                self.assertRegex(output, r"[一-鿿]", "help text should include Chinese")

    def test_keywords_actions_have_help(self):
        for action in ("show", "preview"):
            code, output = run_cli("keywords", action, "--help")
            self.assertEqual(code, 0)
            self.assertIn(f"keywords {action}", output)
        code, output = run_cli("keywords")
        self.assertEqual(code, 0)
        self.assertIn("preview", output)

    def test_legacy_bare_import_options_point_to_import_legacy(self):
        code, output = run_cli("--dry-run")
        self.assertEqual(code, 2)
        self.assertIn("import-legacy", output)

    def test_invalid_port_is_rejected(self):
        code, output = run_cli("serve", "--port", "70000")
        self.assertEqual(code, 2)
        self.assertIn("65535", output)


class SubprocessEntryPointTests(unittest.TestCase):
    """The real `-m paper_feed` and the compatibility shims (no network: --help only)."""

    def run_python(self, *args):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        return subprocess.run([sys.executable, *args], cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", env=env, timeout=60)

    def test_module_without_command_prints_help(self):
        result = self.run_python("-m", "paper_feed")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: python -m paper_feed", result.stdout)

    def test_get_rss_shim_delegates_to_refresh(self):
        result = self.run_python("get_RSS.py", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: python -m paper_feed refresh", result.stdout)
        self.assertIn("exit codes", result.stdout)

    def test_server_shim_delegates_to_serve(self):
        result = self.run_python("server.py", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("usage: python -m paper_feed serve", result.stdout)
        self.assertIn("--port", result.stdout)

    def test_publish_guard_module_still_runs(self):
        result = self.run_python("-m", "paper_feed.publish_guard", "--help")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--baseline-xml", result.stdout)


class ShimDelegationTests(unittest.TestCase):
    def test_get_rss_main_delegates(self):
        with patch("paper_feed.cli.main", return_value=7) as main:
            self.assertEqual(get_RSS.main(["--x"]), 7)
        main.assert_called_once_with(["refresh", "--x"])

    def test_server_main_delegates(self):
        with patch("paper_feed.cli.main", return_value=0) as main:
            self.assertEqual(server.main(["--port", "9001"]), 0)
        main.assert_called_once_with(["serve", "--port", "9001"])


class RefreshAndServeDispatchTests(unittest.TestCase):
    def test_refresh_exit_codes(self):
        cases = [({"published": True, "failed_sources": ["x"]}, 0),
                 ({"published": False, "successful_sources": []}, 1),
                 ({"published": False, "config_error": True}, 2)]
        for outcome, expected in cases:
            with self.subTest(expected=expected), patch.object(get_RSS, "run_rss_flow", return_value=outcome) as flow:
                code, _ = run_cli("refresh")
            self.assertEqual(code, expected)
            flow.assert_called_once_with()

    def test_serve_passes_port_host_and_open(self):
        with patch.object(server, "run_server", return_value=0) as run_server:
            self.assertEqual(run_cli("serve", "--port", "18001")[0], 0)
            run_server.assert_called_once_with(18001, host="127.0.0.1", on_ready=None)
            run_server.reset_mock()
            self.assertEqual(run_cli("serve", "--port", "18002", "--host", "127.0.0.1", "--open")[0], 0)
            run_server.assert_called_once_with(18002, host="127.0.0.1", on_ready=cli.open_browser)

    def test_serve_uses_paper_feed_port(self):
        with clean_env(PAPER_FEED_PORT="18003"), patch.object(server, "run_server", return_value=0) as run_server:
            run_cli("serve")
        self.assertEqual(run_server.call_args.args[0], 18003)

    def test_start_opens_running_paper_feed_without_serving(self):
        with patch.object(cli, "probe_port", return_value="paper_feed"), \
                patch.object(cli, "open_browser") as opener, \
                patch.object(server, "run_server") as run_server, \
                patch.object(get_RSS, "run_rss_flow") as flow:
            code, output = run_cli("start", "--port", "18004")
        self.assertEqual(code, 0)
        opener.assert_called_once_with("http://127.0.0.1:18004/")
        run_server.assert_not_called()
        flow.assert_not_called()
        self.assertIn("already running", output)

    def test_start_refuses_port_held_by_another_program(self):
        with patch.object(cli, "probe_port", return_value="busy"), \
                patch.object(server, "run_server") as run_server:
            code, output = run_cli("start", "--port", "18005")
        self.assertEqual(code, 1)
        run_server.assert_not_called()
        self.assertIn("--port 8001", output)
        # The hint names the interpreter actually in use, not a bare `python`.
        self.assertIn(f"{cli.python_command()} -m paper_feed start --port 8001", output)
        self.assertIn(sys.executable, output)

    def test_start_serves_without_network(self):
        with patch.object(cli, "probe_port", return_value="free"), \
                patch.object(server, "run_server", return_value=0) as run_server, \
                patch.object(get_RSS, "run_rss_flow") as flow:
            self.assertEqual(run_cli("start", "--port", "18006")[0], 0)
            run_server.assert_called_once_with(18006, host="127.0.0.1", on_ready=cli.open_browser)
            run_server.reset_mock()
            run_cli("start", "--port", "18006", "--no-browser")
            run_server.assert_called_once_with(18006, host="127.0.0.1", on_ready=None)
        flow.assert_not_called()

    def test_run_refreshes_then_serves_even_when_refresh_fails(self):
        with patch.object(cli, "probe_port", return_value="free"), \
                patch.object(server, "run_server", return_value=0) as run_server, \
                patch.object(get_RSS, "get_config", return_value={"OPENAI_API_KEY": "sk-test"}), \
                patch.object(get_RSS, "run_rss_flow", return_value={"published": False, "config_error": True}) as flow:
            code, output = run_cli("run", "--port", "18007")
        self.assertEqual(code, 0)
        flow.assert_called_once_with()
        run_server.assert_called_once()
        self.assertIn("costs money", output)
        self.assertIn("did not publish", output)
        self.assertNotIn("sk-test", output)

    def test_run_with_existing_paper_feed_refreshes_then_opens(self):
        with patch.object(cli, "probe_port", return_value="paper_feed"), \
                patch.object(cli, "open_browser") as opener, \
                patch.object(server, "run_server") as run_server, \
                patch.object(get_RSS, "get_config", return_value={}), \
                patch.object(get_RSS, "run_rss_flow", return_value={"published": True}) as flow:
            code, output = run_cli("run", "--port", "18008")
        self.assertEqual(code, 0)
        flow.assert_called_once_with()
        opener.assert_called_once()
        run_server.assert_not_called()
        self.assertNotIn("costs money", output)

    def test_probe_port_classifies_listeners(self):
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
            self.assertEqual(cli.probe_port(port), "free")  # bound but not listening
            listener.listen()
            with patch("urllib.request.urlopen", side_effect=OSError("not http")):
                self.assertEqual(cli.probe_port(port), "busy")


class AiCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.database = Path(self.temp.name) / "paper_feed.sqlite3"
        self.ids = make_database(self.database)
        save_translations(str(self.database), {
            self.ids["Alpha paper"]: {"zh": "甲", "classification_version": get_RSS.CLASSIFICATION_VERSION}})
        set_state(self.database, self.ids["Alpha paper"], "favorite")
        set_state(self.database, self.ids["Beta paper"], "favorite")
        save_abstracts(str(self.database), {self.ids["Alpha paper"]: {"abstract": "x", "source": "gpt_summarized"}})
        self.env = clean_env(PAPER_FEED_DB=str(self.database))
        self.env.start()
        self.config = patch.object(get_RSS, "get_config",
                                   return_value={"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "test-model"})
        self.config.start()

    def tearDown(self):
        self.config.stop()
        self.env.stop()
        self.temp.cleanup()

    def test_reanalyze_dry_run_counts_without_calling_ai(self):
        with patch.object(get_RSS, "run_reanalysis_flow") as flow:
            code, output = run_cli("reanalyze", "--dry-run")
        self.assertEqual(code, 0)
        flow.assert_not_called()
        self.assertIn("2 paper(s) need title analysis", output)
        self.assertIn("--dry-run", output)

    def test_reanalyze_requires_confirmation(self):
        with patch.object(get_RSS, "run_reanalysis_flow") as flow, \
                patch.object(cli, "_stdin_is_interactive", return_value=False):
            code, output = run_cli("reanalyze")
        self.assertEqual(code, 1)
        flow.assert_not_called()
        self.assertIn("--yes", output)
        with patch.object(get_RSS, "run_reanalysis_flow") as flow, \
                patch.object(cli, "_stdin_is_interactive", return_value=True), \
                patch("builtins.input", return_value="n"):
            self.assertEqual(run_cli("reanalyze")[0], 1)
        flow.assert_not_called()

    def test_reanalyze_runs_after_confirmation(self):
        result = {"status": "ok", "message": "Updated 2 paper analyses.", "failed": 0}
        with patch.object(get_RSS, "run_reanalysis_flow", return_value=result) as flow:
            code, output = run_cli("reanalyze", "--yes")
        self.assertEqual(code, 0)
        flow.assert_called_once_with()
        self.assertIn("Updated 2", output)
        with patch.object(get_RSS, "run_reanalysis_flow", return_value=result) as flow, \
                patch.object(cli, "_stdin_is_interactive", return_value=True), \
                patch("builtins.input", return_value="y"):
            self.assertEqual(run_cli("reanalyze")[0], 0)
        flow.assert_called_once_with()

    def test_reanalyze_without_key(self):
        with patch.object(get_RSS, "get_config", return_value={}), \
                patch.object(get_RSS, "run_reanalysis_flow") as flow:
            self.assertEqual(run_cli("reanalyze", "--dry-run")[0], 0)
            code, output = run_cli("reanalyze", "--yes")
        self.assertEqual(code, 1)
        flow.assert_not_called()
        self.assertIn("OPENAI_API_KEY", output)

    def test_summarize_favorites_dry_run_counts_pending(self):
        with patch.object(get_RSS, "summarize_specific_papers") as summarize:
            code, output = run_cli("summarize-favorites", "--dry-run")
        self.assertEqual(code, 0)
        summarize.assert_not_called()
        self.assertIn("2 favorite(s); 1 still need an AI summary", output)

    def test_summarize_favorites_runs_with_yes(self):
        result = {"status": "ok", "message": "Successfully summarized 1 papers.", "failed": 0}
        with patch.object(get_RSS, "summarize_specific_papers", return_value=result) as summarize:
            code, _ = run_cli("summarize-favorites", "--yes")
        self.assertEqual(code, 0)
        self.assertEqual(sorted(summarize.call_args.args[0]), ["legacy-0", "legacy-1"])

    def test_summarize_favorites_prints_fetched_and_skipped(self):
        result = {"status": "ok", "message": "Fetched 1 abstracts; AI summary skipped.", "updated": 0,
                  "fetched": 1, "ai_skipped": True, "failed": 0}
        with patch.object(get_RSS, "summarize_specific_papers", return_value=result):
            code, output = run_cli("summarize-favorites", "--yes")
        self.assertEqual(code, 0)
        self.assertIn("Free abstracts fetched by DOI: 1", output)
        self.assertIn("AI summary skipped", output)

    def test_summarize_favorites_without_key_offers_free_abstract_fetch(self):
        result = {"status": "ok", "message": "Fetched 1 abstracts; 0 not found; 0 skipped.",
                  "fetched": 1, "failed": 0, "skipped": 0, "errors": []}
        with patch.object(get_RSS, "get_config", return_value={}), \
                patch.object(get_RSS, "summarize_specific_papers") as summarize, \
                patch.object(get_RSS, "fetch_missing_abstracts", return_value=result) as fetch:
            code, output = run_cli("summarize-favorites", "--dry-run")
            self.assertEqual(code, 0)
            fetch.assert_not_called()
            with patch.object(cli, "_stdin_is_interactive", return_value=False):
                self.assertEqual(run_cli("summarize-favorites")[0], 1)
            fetch.assert_not_called()
            code, output = run_cli("summarize-favorites", "--yes")
        self.assertEqual(code, 0)
        summarize.assert_not_called()
        fetch.assert_called_once_with([self.ids["Beta paper"]])
        self.assertIn("AI summaries are skipped", output)
        self.assertIn("Fetched / 已获取: 1", output)

    def test_fetch_abstracts_command(self):
        result = {"status": "ok", "message": "Fetched 2 abstracts; 1 not found; 0 skipped.",
                  "fetched": 2, "failed": 1, "skipped": 0, "errors": []}
        with patch.object(get_RSS, "fetch_missing_abstracts", return_value=result) as fetch:
            with patch.object(cli, "_stdin_is_interactive", return_value=False):
                code, output = run_cli("fetch-abstracts")
            self.assertEqual(code, 1)
            fetch.assert_not_called()
            code, output = run_cli("fetch-abstracts", "--view", "all", "--yes")
        self.assertEqual(code, 0)
        fetch.assert_called_once_with(view="all")
        self.assertIn("3 paper(s) in view 'all'", output)
        self.assertIn("Fetched / 已获取: 2", output)
        code, output = run_cli("fetch-abstracts", "--view", "bogus")
        self.assertEqual(code, 2)
        with patch.object(get_RSS, "fetch_missing_abstracts", return_value={"status": "error"}):
            self.assertEqual(run_cli("fetch-abstracts", "--yes")[0], 1)

    def test_dry_run_with_missing_database_does_not_create_it(self):
        missing = Path(self.temp.name) / "absent.sqlite3"
        with patch.dict(os.environ, {"PAPER_FEED_DB": str(missing)}):
            for command in ("reanalyze", "summarize-favorites"):
                code, output = run_cli(command, "--dry-run")
                self.assertEqual(code, 0)
                self.assertIn("not found", output)
        self.assertFalse(missing.exists())


class KeywordCommandTests(unittest.TestCase):
    def test_show_reports_environment_override(self):
        with clean_env(RSS_KEYWORDS="consumer AND AI;-only exclusion"):
            code, output = run_cli("keywords", "show")
        self.assertEqual(code, 0)
        self.assertIn("RSS_KEYWORDS", output)
        self.assertIn("consumer AND AI", output)
        self.assertIn("matches nothing", output)

    def test_preview_uses_text_file_or_active_rules(self):
        result = {"total_papers": 4, "matched": 1, "terms": [{"term": "consumer", "count": 2}],
                  "samples": [{"paper_id": "p", "title": "Consumer study"}]}
        with patch.object(server, "keyword_preview", return_value=result) as preview:
            code, output = run_cli("keywords", "preview", "--text", "consumer")
            self.assertEqual(code, 0)
            preview.assert_called_with("consumer")
            self.assertIn("Matched / 命中: 1 (25.0%)", output)
            self.assertIn("Consumer study", output)
            with tempfile.TemporaryDirectory() as temp:
                rules = Path(temp) / "rules.txt"
                rules.write_text("shame\n", encoding="utf-8")
                run_cli("keywords", "preview", "--file", str(rules))
            preview.assert_called_with("shame\n")
            with clean_env(RSS_KEYWORDS="a;b"):
                code, output = run_cli("keywords", "preview", "--json")
            preview.assert_called_with("a\nb")
            self.assertEqual(json.loads(output), result)


class StorageCommandTests(unittest.TestCase):
    def test_backup_and_import_legacy(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "paper_feed.sqlite3"
            make_database(database)
            code, output = run_cli("backup", "--database", str(database), "--out", str(Path(temp) / "out"))
            self.assertEqual(code, 0)
            self.assertIn("Backup written (3 papers", output)
            self.assertIn("备份完成", output)
            backups = list((Path(temp) / "out").glob("paper_feed.sqlite3-backup-*.sqlite3"))
            self.assertEqual(len(backups), 1)
            self.assertIn(str(backups[0]), output)
            self.assertIn("-m paper_feed restore", output)
            code, output = run_cli("backup", "--json", "--database", str(database), "--out", str(Path(temp) / "out"))
            self.assertEqual(code, 0)
            self.assertEqual(json.loads(output)["papers"], 3)
            code, output = run_cli("backup", "--database", str(Path(temp) / "missing.sqlite3"))
            self.assertEqual(code, 1)
            code, output = run_cli("import-legacy", "--root", temp, "--database", str(Path(temp) / "new.sqlite3"),
                                   "--dry-run")
            self.assertEqual(code, 0)
            self.assertIsInstance(json.loads(output), dict)

    def test_publish_guard_wraps_existing_cli(self):
        with patch.object(publish_guard, "validate_exports", return_value=5) as validate:
            code, output = run_cli("publish-guard", "--xml", "a.xml", "--json", "b.json",
                                   "--baseline-xml", "old.xml", "--projection-limit", "50")
        self.assertEqual(code, 0)
        validate.assert_called_once_with(Path("a.xml"), Path("b.json"), Path("old.xml"), 50)
        self.assertIn("passed: 5", output)
        with patch.object(publish_guard, "validate_exports", side_effect=publish_guard.PublishGuardError("bad")):
            self.assertEqual(run_cli("publish-guard", "--xml", "a", "--json", "b")[0], 1)


class RestoreCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "data" / "paper_feed.sqlite3"
        self.database.parent.mkdir()
        # Backup holds 3 papers with a favorite; the live database later diverges.
        ids = make_database(self.database)
        set_state(self.database, ids["Alpha paper"], "favorite")
        self.backup = Path(json.loads(
            run_cli("backup", "--json", "--database", str(self.database), "--out", str(self.root / "bk"))[1])["backup"])
        self.database.unlink()
        for suffix in ("-wal", "-shm"):
            Path(str(self.database) + suffix).unlink(missing_ok=True)
        make_database(self.database, titles=("Only live paper",))
        self.probe = patch.object(cli, "probe_port", return_value="free")
        self.probe.start()

    def tearDown(self):
        self.probe.stop()
        self.temp.cleanup()

    def papers(self, path=None):
        conn = connect(str(path or self.database))
        try:
            titles = sorted(row[0] for row in conn.execute("SELECT title FROM papers"))
            states = dict(conn.execute("SELECT p.title, s.state FROM papers p JOIN paper_review_state s USING(paper_id)"))
        finally:
            conn.close()
        return titles, states

    def restore(self, *extra):
        return run_cli("restore", str(self.backup), "--database", str(self.database), *extra)

    def test_restore_replaces_database_after_safety_backup(self):
        Path(str(self.database) + "-wal").write_bytes(b"")  # stale sidecar must not survive
        code, output = self.restore("--yes")
        self.assertEqual(code, 0, output)
        titles, states = self.papers()
        self.assertEqual(titles, ["Alpha paper", "Beta paper", "Gamma paper"])
        self.assertEqual(states["Alpha paper"], "favorite")
        safety = list(self.database.parent.glob("paper_feed.sqlite3-pre-restore-*.sqlite3"))
        self.assertEqual(len(safety), 1)
        self.assertEqual(self.papers(safety[0])[0], ["Only live paper"])
        self.assertIn(str(safety[0]), output)
        self.assertIn("恢复完成", output)
        self.assertFalse(Path(str(self.database) + ".restore-tmp").exists())

    def test_restore_requires_confirmation(self):
        with patch.object(cli, "_stdin_is_interactive", return_value=False):
            code, output = self.restore()
        self.assertEqual(code, 1)
        self.assertIn("--yes", output)
        self.assertEqual(self.papers()[0], ["Only live paper"])
        with patch.object(cli, "_stdin_is_interactive", return_value=True), \
                patch("builtins.input", return_value="n"):
            self.assertEqual(self.restore()[0], 1)
        self.assertEqual(self.papers()[0], ["Only live paper"])
        with patch.object(cli, "_stdin_is_interactive", return_value=True), \
                patch("builtins.input", return_value="y"):
            self.assertEqual(self.restore()[0], 0)
        self.assertEqual(len(self.papers()[0]), 3)

    def test_restore_refuses_while_paper_feed_server_is_running(self):
        with patch.object(cli, "probe_port", return_value="paper_feed") as probe:
            code, output = self.restore("--yes", "--port", "18011")
        self.assertEqual(code, 1)
        probe.assert_called_once_with(18011, "127.0.0.1")
        self.assertIn("running", output)
        self.assertEqual(self.papers()[0], ["Only live paper"])
        self.assertEqual(list(self.database.parent.glob("*pre-restore*")), [])

    def test_restore_rejects_missing_corrupt_or_foreign_files(self):
        code, output = run_cli("restore", str(self.root / "missing.sqlite3"), "--database", str(self.database), "--yes")
        self.assertEqual(code, 1)
        self.assertIn("not found", output)
        garbage = self.root / "garbage.sqlite3"
        garbage.write_bytes(b"this is not a database" * 100)
        code, output = run_cli("restore", str(garbage), "--database", str(self.database), "--yes")
        self.assertEqual(code, 1)
        self.assertIn("not a readable SQLite database", output)
        foreign = self.root / "foreign.sqlite3"
        conn = sqlite3.connect(str(foreign))
        conn.execute("CREATE TABLE other(x)")
        conn.commit()
        conn.close()
        code, output = run_cli("restore", str(foreign), "--database", str(self.database), "--yes")
        self.assertEqual(code, 1)
        self.assertIn("not a Paper Feed database", output)
        code, output = run_cli("restore", str(self.database), "--database", str(self.database), "--yes")
        self.assertEqual(code, 1)
        self.assertEqual(self.papers()[0], ["Only live paper"])
        self.assertEqual(list(self.database.parent.glob("*pre-restore*")), [])

    def test_restore_into_missing_database(self):
        self.database.unlink()
        code, output = self.restore("--yes")
        self.assertEqual(code, 0, output)
        self.assertEqual(len(self.papers()[0]), 3)
        self.assertIn("does not exist yet", output)


class DoctorTests(unittest.TestCase):
    SECRET = "sk-very-secret-value-1234567890"

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "journals.dat").write_text("# comment\nhttps://example.test/rss\n", encoding="utf-8")
        (self.root / "keywords.dat").write_text("consumer\n", encoding="utf-8")
        (self.root / "web").mkdir()
        for name in ("index.html", "app.js"):
            (self.root / "web" / name).write_text("x", encoding="utf-8")
        (self.root / "config.json").write_text(json.dumps({
            "OPENAI_API_KEY": "your-api-key-here", "OPENAI_MODEL": "deepseek-chat",
            "OPENAI_BASE_URL": "https://user:pw@api.example.test/v1?token=abc"}), encoding="utf-8")
        self.ids = make_database(self.root / "data" / "paper_feed.sqlite3")
        set_state(self.root / "data" / "paper_feed.sqlite3", self.ids["Alpha paper"], "favorite")
        self.probe = patch.object(cli, "probe_port", return_value="free")
        self.probe.start()

    def tearDown(self):
        self.probe.stop()
        self.temp.cleanup()

    def doctor(self, *extra, **env):
        with clean_env(**env):
            return run_cli("doctor", "--root", str(self.root), "--ascii", *extra)

    def test_healthy_project_passes_and_hides_secrets(self):
        code, output = self.doctor(OPENAI_API_KEY=self.SECRET, OPENAI_PROXY="http://u:p@proxy.test:1")
        self.assertEqual(code, 0, output)
        self.assertNotIn("[FAIL]", output)
        self.assertNotIn(self.SECRET, output)
        self.assertNotIn("u:p@", output)
        self.assertNotIn("pw@", output)
        self.assertNotIn("token=abc", output)
        self.assertIn("OPENAI_API_KEY", output)
        self.assertIn("source: env", output)
        self.assertIn("deepseek-chat (source: config.json)", output)
        self.assertIn("https://api.example.test", output)
        self.assertIn("integrity_check: ok", output)
        self.assertIn("3 total (inbox 2, favorite 1, archived 0, hidden 0)", output)
        self.assertIn("1 journal feed(s)", output)

    def test_placeholder_key_is_a_warning_not_a_failure(self):
        code, output = self.doctor()
        self.assertEqual(code, 0, output)
        self.assertIn("placeholder ignored", output)
        self.assertIn("[WARN]", output)

    def test_blocking_problems_fail(self):
        (self.root / "keywords.dat").write_text("# only a comment\n", encoding="utf-8")
        (self.root / "web" / "app.js").unlink()
        code, output = self.doctor()
        self.assertEqual(code, 1)
        self.assertIn("[FAIL]", output)
        self.assertIn("keywords.dat", output)
        self.assertIn("missing app.js", output)
        self.assertIn("blocking problem", output)

    def test_env_override_and_corrupt_database(self):
        (self.root / "keywords.dat").unlink()
        database = self.root / "corrupt.sqlite3"
        database.write_bytes(b"this is not a sqlite database" * 100)
        code, output = self.doctor("--database", str(database), RSS_KEYWORDS="a;b")
        self.assertEqual(code, 1)
        self.assertIn("overridden by RSS_KEYWORDS (2", output)
        self.assertRegex(output, r"\[FAIL\]\s+SQLite database")

    def test_missing_database_is_only_a_warning(self):
        code, output = self.doctor("--database", str(self.root / "none.sqlite3"))
        self.assertEqual(code, 0, output)
        self.assertIn("not found", output)
        self.assertFalse((self.root / "none.sqlite3").exists())

    def test_port_states(self):
        for state, expected in (("paper_feed", "already running"), ("busy", "another program")):
            with patch.object(cli, "probe_port", return_value=state):
                code, output = self.doctor("--port", "18009")
            self.assertEqual(code, 0)
            self.assertIn("Port 18009", output)
            self.assertIn(expected, output)

    def test_unicode_marks_when_console_supports_them(self):
        with patch.object(cli, "_ORIGINAL_STDOUT_ENCODING", "utf-8"), clean_env():
            code, output = run_cli("doctor", "--root", str(self.root))
        self.assertEqual(code, 0)
        self.assertIn("✓", output)
        with patch.object(cli, "_ORIGINAL_STDOUT_ENCODING", "gbk"), clean_env():
            _, output = run_cli("doctor", "--root", str(self.root))
        self.assertNotIn("✓", output)
        self.assertIn("[OK]", output)


if __name__ == "__main__":
    unittest.main()
