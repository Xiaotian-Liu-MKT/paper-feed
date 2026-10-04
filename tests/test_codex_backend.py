"""Codex CLI AI backend: client, error mapping, backend resolution and gating.

Fully offline: ``subprocess.run`` is always mocked and Codex discovery is
patched, so a real ``codex exec`` is never spawned.
"""
import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import get_RSS
from ai_test_guard import REAL_CLI_FIND_CODEX, REAL_FIND_CODEX
from ai_test_guard import setUpModule, tearDownModule  # noqa: E402,F401 (no real Codex CLI)
from paper_feed import cli

FAKE_CODEX = r"C:\fake\npm\codex.cmd" if os.name == "nt" else "/fake/bin/codex"


def settings(**overrides):
    values = {"executable": FAKE_CODEX, "model": "gpt-6-luna", "reasoning_effort": "low"}
    values.update(overrides)
    return get_RSS.CodexCLISettings(**values)


class FakeCodex:
    """Stand-in for subprocess.run: records calls and writes the -o file."""

    def __init__(self, output="ok", returncode=0, stderr=b"", exception=None):
        self.output = output
        self.returncode = returncode
        self.stderr = stderr
        self.exception = exception
        self.calls = []

    def __call__(self, cmd, **kwargs):
        workdir = kwargs.get("cwd")
        self.calls.append({"cmd": list(cmd), "kwargs": kwargs, "cwd_existed": bool(workdir and os.path.isdir(workdir))})
        if self.exception is not None:
            raise self.exception
        output = self.output(cmd, kwargs) if callable(self.output) else self.output
        if output is not None:
            path = cmd[cmd.index("-o") + 1]
            with open(path, "w", encoding="utf-8") as handle:
                handle.write(output)
        return subprocess.CompletedProcess(cmd, self.returncode, b"", self.stderr)

    @property
    def prompts(self):
        return [call["kwargs"]["input"].decode("utf-8") for call in self.calls]


def run_cli(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        try:
            code = cli.main(list(argv))
        except SystemExit as exit_:
            code = exit_.code
    return code, out.getvalue()


class PromptAndOutputTests(unittest.TestCase):
    def test_flatten_puts_system_first_and_adds_json_instruction(self):
        prompt = get_RSS.flatten_messages([
            {"role": "user", "content": "Titles: 1. A"},
            {"role": "system", "content": "You are a JSON-only API."},
        ], json_output=True)
        self.assertTrue(prompt.startswith("You are a JSON-only API."))
        self.assertLess(prompt.index("JSON-only"), prompt.index("Titles: 1. A"))
        self.assertTrue(prompt.endswith("Output only valid JSON, no code fences and no other text."))
        self.assertNotIn("valid JSON, no code fences", get_RSS.flatten_messages([{"role": "user", "content": "hi"}]))

    def test_flatten_accepts_content_parts(self):
        prompt = get_RSS.flatten_messages([{"role": "user", "content": [{"type": "text", "text": "part one"}]}])
        self.assertEqual(prompt, "part one")

    def test_strip_code_fences(self):
        self.assertEqual(get_RSS.strip_code_fences('```json\n{"a": 1}\n```'), '{"a": 1}')
        self.assertEqual(get_RSS.strip_code_fences('```\nplain\n```\n'), "plain")
        self.assertEqual(get_RSS.strip_code_fences('  {"a": "```"}  '), '{"a": "```"}')


class CodexClientTests(unittest.TestCase):
    def test_create_runs_codex_exec_and_returns_openai_shaped_response(self):
        fake = FakeCodex(output='```json\n{"results": []}\n```')
        client = get_RSS.make_openai_client(settings(), None, None)
        self.assertIsInstance(client, get_RSS.CodexCLIClient)
        with patch.object(subprocess, "run", side_effect=fake):
            response = client.chat.completions.create(
                model="gpt-4o-mini", max_tokens=5, temperature=0.3,
                response_format={"type": "json_object"},
                messages=[{"role": "system", "content": "SYSTEM"}, {"role": "user", "content": "标题：消费者"}])
        self.assertEqual(response.choices[0].message.content, '{"results": []}')
        self.assertIsNone(response.usage)
        call = fake.calls[0]
        cmd = call["cmd"]
        self.assertEqual(cmd[0], FAKE_CODEX)
        self.assertEqual(cmd[1:4], ["exec", "-m", "gpt-6-luna"])  # OpenAI model name ignored
        self.assertIn("model_reasoning_effort=low", cmd)
        for flag in ("--ephemeral", "--skip-git-repo-check", "--ignore-rules"):
            self.assertIn(flag, cmd)
        self.assertEqual(cmd[cmd.index("-s") + 1], "read-only")
        self.assertEqual(cmd[-1], "-")
        self.assertTrue(call["cwd_existed"])
        self.assertFalse(os.path.exists(call["kwargs"]["cwd"]), "temp dir must be removed")
        self.assertEqual(os.path.dirname(cmd[cmd.index("-o") + 1]), call["kwargs"]["cwd"])
        self.assertEqual(call["kwargs"]["timeout"], get_RSS.CODEX_TIMEOUT_SECONDS)
        self.assertIsInstance(call["kwargs"]["input"], bytes)
        prompt = fake.prompts[0]
        self.assertTrue(prompt.startswith("SYSTEM"))
        self.assertIn("标题：消费者", prompt)
        self.assertIn("Output only valid JSON", prompt)
        if os.name == "nt":
            self.assertEqual(call["kwargs"]["creationflags"], subprocess.CREATE_NO_WINDOW)
        self.assertNotIn("shell", call["kwargs"])

    def test_with_options_sets_timeout(self):
        fake = FakeCodex()
        client = get_RSS.CodexCLIClient(settings()).with_options(max_retries=0, timeout=120)
        with patch.object(subprocess, "run", side_effect=fake):
            client.chat.completions.create(messages=[{"role": "user", "content": "ping"}])
        self.assertEqual(fake.calls[0]["kwargs"]["timeout"], 120)

    def test_timeout_counts_toward_breaker_without_retry(self):
        fake = FakeCodex(exception=subprocess.TimeoutExpired(["codex"], 180, stderr=b"still thinking"))
        client = get_RSS.CodexCLIClient(settings())
        breaker = get_RSS.CircuitBreaker(2, "Codex CLI")
        with patch.object(subprocess, "run", side_effect=fake), patch.object(get_RSS.time, "sleep") as sleep:
            for _ in range(2):
                with self.assertRaises(get_RSS.CodexCLITimeoutError) as raised:
                    get_RSS.chat_completion_with_retry(client, breaker=breaker, attempts=3,
                                                       messages=[{"role": "user", "content": "x"}])
                self.assertIn("still thinking", str(raised.exception))
            with self.assertRaises(get_RSS.CircuitOpenError):
                get_RSS.chat_completion_with_retry(client, breaker=breaker,
                                                   messages=[{"role": "user", "content": "x"}])
        self.assertEqual(len(fake.calls), 2)  # never retried, then the breaker opened
        sleep.assert_not_called()

    def test_non_zero_exit_reports_stderr_tail_and_is_not_retried(self):
        fake = FakeCodex(output=None, returncode=1, stderr=b"x" * 1000 + b" Error: not logged in")
        client = get_RSS.CodexCLIClient(settings())
        with patch.object(subprocess, "run", side_effect=fake), patch.object(get_RSS.time, "sleep"):
            with self.assertRaises(get_RSS.CodexCLIProcessError) as raised:
                get_RSS.chat_completion_with_retry(client, attempts=3, messages=[{"role": "user", "content": "x"}])
        message = str(raised.exception)
        self.assertIn("exited with code 1", message)
        self.assertIn("not logged in", message)
        self.assertLess(len(message), 400)
        self.assertEqual(len(fake.calls), 1)

    def test_stderr_never_leaks_openai_key(self):
        fake = FakeCodex(output=None, returncode=2, stderr=b"auth failed for sk-secret-123456")
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-secret-123456"}), \
                patch.object(subprocess, "run", side_effect=fake):
            with self.assertRaises(get_RSS.CodexCLIProcessError) as raised:
                get_RSS.run_codex_exec(settings(), "x")
        self.assertNotIn("sk-secret-123456", str(raised.exception))

    def test_empty_output_is_an_error(self):
        fake = FakeCodex(output="   ")
        with patch.object(subprocess, "run", side_effect=fake):
            with self.assertRaises(get_RSS.CodexCLIProcessError):
                get_RSS.run_codex_exec(settings(), "x")

    def test_missing_executable_fails_fast(self):
        fake = FakeCodex(exception=FileNotFoundError(2, "not found"))
        client = get_RSS.CodexCLIClient(settings())
        breaker = get_RSS.CircuitBreaker(2, "Codex CLI")
        with patch.object(subprocess, "run", side_effect=fake), patch.object(get_RSS.time, "sleep") as sleep:
            with self.assertRaises(get_RSS.CodexCLINotFoundError):
                get_RSS.chat_completion_with_retry(client, breaker=breaker, attempts=3,
                                                   messages=[{"role": "user", "content": "x"}])
        self.assertEqual(len(fake.calls), 1)
        sleep.assert_not_called()
        self.assertFalse(get_RSS.is_retryable_openai_error(get_RSS.CodexCLINotFoundError("x")))

    def test_gpt_helpers_use_codex(self):
        fake = FakeCodex(output="【基于标题推测，未读原文】可能研究……")
        with patch.object(subprocess, "run", side_effect=fake):
            text = get_RSS.generate_abstract_with_gpt("A title", "J", settings())
            summary = get_RSS.summarize_abstract_with_gpt("An abstract", "A title", settings())
        self.assertTrue(text.startswith("【基于标题推测"))
        self.assertTrue(summary)
        self.assertEqual(len(fake.calls), 2)
        self.assertNotIn("Output only valid JSON", fake.prompts[0])


class ResolverTests(unittest.TestCase):
    def test_codex_found_is_the_default_backend(self):
        with patch.object(get_RSS, "find_codex_executable", return_value=FAKE_CODEX):
            result = get_RSS.ai_settings({"OPENAI_API_KEY": None})
        self.assertEqual((result["backend"], result["ready"], result["model"]), ("codex", True, "gpt-6-luna"))
        self.assertTrue(get_RSS.is_codex_backend(result["api_key"]))
        self.assertTrue(result["api_key"])
        self.assertEqual(result["api_key"].executable, FAKE_CODEX)

    def test_codex_settings_from_config(self):
        config = {"AI_BACKEND": "CODEX", "CODEX_MODEL": "gpt-x", "CODEX_REASONING_EFFORT": "medium",
                  "OPENAI_API_KEY": "sk-test"}
        with patch.object(get_RSS, "find_codex_executable", return_value=FAKE_CODEX):
            result = get_RSS.ai_settings(config)
        self.assertEqual(result["backend"], "codex")
        self.assertEqual((result["api_key"].model, result["api_key"].reasoning_effort), ("gpt-x", "medium"))

    def test_unsafe_codex_model_is_not_ready(self):
        with patch.object(get_RSS, "find_codex_executable", return_value=FAKE_CODEX):
            result = get_RSS.ai_settings({"CODEX_MODEL": 'gpt" & calc'})
        self.assertFalse(result["ready"])
        self.assertIn("CODEX_MODEL", result["reason"])

    def test_codex_missing_with_key_falls_back_to_openai(self):
        out = io.StringIO()
        with patch.object(get_RSS, "_CODEX_FALLBACK_LOGGED", False), contextlib.redirect_stdout(out):
            result = get_RSS.ai_settings({"OPENAI_API_KEY": "sk-test", "OPENAI_MODEL": "m1"}, log=True)
            get_RSS.ai_settings({"OPENAI_API_KEY": "sk-test"}, log=True)
        self.assertEqual((result["backend"], result["api_key"], result["model"], result["ready"]),
                         ("openai", "sk-test", "m1", True))
        self.assertFalse(result["codex_available"])
        self.assertEqual(out.getvalue().count("falling back to the OpenAI API"), 1)

    def test_neither_backend_is_not_ready(self):
        result = get_RSS.ai_settings({})
        self.assertFalse(result["ready"])
        self.assertIsNone(result["backend"])
        self.assertIn("Codex CLI not found", result["reason"])
        self.assertIn("OPENAI_API_KEY", result["reason"])

    def test_openai_backend_keeps_current_behaviour(self):
        with patch.object(get_RSS, "find_codex_executable", return_value=FAKE_CODEX) as finder:
            ready = get_RSS.ai_settings({"AI_BACKEND": "openai", "OPENAI_API_KEY": "sk-test"})
            missing = get_RSS.ai_settings({"AI_BACKEND": "openai"})
        finder.assert_not_called()
        self.assertEqual((ready["backend"], ready["api_key"]), ("openai", "sk-test"))
        self.assertFalse(missing["ready"])
        self.assertIn("AI_BACKEND=openai", missing["reason"])

    def test_get_config_defaults_and_env_precedence(self):
        with tempfile.TemporaryDirectory() as temp:
            path = os.path.join(temp, "config.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump({"AI_BACKEND": "openai", "CODEX_MODEL": "file-model"}, handle)
            env = {key: value for key, value in os.environ.items() if key not in get_RSS.CONFIG_KEYS}
            with patch.object(get_RSS, "CONFIG_FILE", path), patch.dict(os.environ, env, clear=True):
                from_file = get_RSS.get_config()
                with patch.dict(os.environ, {"AI_BACKEND": "codex", "CODEX_MODEL": ""}):
                    from_env = get_RSS.get_config()
        self.assertEqual((from_file["AI_BACKEND"], from_file["CODEX_MODEL"]), ("openai", "file-model"))
        self.assertEqual(from_file["CODEX_REASONING_EFFORT"], "low")
        self.assertIsNone(from_file["CODEX_PATH"])
        self.assertEqual((from_env["AI_BACKEND"], from_env["CODEX_MODEL"]), ("codex", "file-model"))

    def test_find_codex_executable_honours_codex_path(self):
        with tempfile.TemporaryDirectory() as temp:
            shim = os.path.join(temp, "codex")
            target = shim + ".cmd" if os.name == "nt" else shim
            Path(target).write_text("@echo off\n", encoding="utf-8")
            self.assertEqual(REAL_FIND_CODEX({"CODEX_PATH": shim}), target)
            self.assertEqual(REAL_CLI_FIND_CODEX(shim), target)
        with patch("shutil.which", return_value=None):
            self.assertIsNone(REAL_FIND_CODEX({}))
        with patch("shutil.which", return_value=FAKE_CODEX) as which:
            self.assertEqual(REAL_FIND_CODEX({"CODEX_PATH": ""}), FAKE_CODEX)
            self.assertEqual(REAL_CLI_FIND_CODEX(None), FAKE_CODEX)
        self.assertEqual(which.call_args_list[0].args, ("codex",))

    def test_cli_mirrors_config_constants(self):
        self.assertEqual(cli.CONFIG_KEYS, get_RSS.CONFIG_KEYS)
        self.assertEqual(cli.CONFIG_DEFAULTS, get_RSS.CONFIG_DEFAULTS)
        self.assertEqual(cli.AI_BACKENDS, get_RSS.AI_BACKENDS)


class GatingTests(unittest.TestCase):
    def batch_output(self, cmd, kwargs):
        prompt = kwargs["input"].decode("utf-8")
        titles = [line.split(". ", 1)[1] for line in prompt.split("Titles:\n", 1)[1].splitlines()
                  if ". " in line and line.split(". ", 1)[0].isdigit()]
        return json.dumps({"results": [{"index": i, "zh": f"译{i}", "methods": [], "topics": []}
                                       for i, _ in enumerate(titles, 1)]})

    def test_batch_analysis_uses_large_chunks_and_two_workers(self):
        titles = [f"Consumer title {n}" for n in range(30)]
        fake = FakeCodex(output=self.batch_output)
        with patch.object(subprocess, "run", side_effect=fake), \
                patch.object(get_RSS, "load_categories", return_value={}), \
                patch.object(get_RSS, "ThreadPoolExecutor", wraps=get_RSS.ThreadPoolExecutor) as pool:
            report = {}
            results = get_RSS.batch_analyze_papers(titles, settings(), report=report)
        self.assertEqual(len(fake.calls), 2)  # 25 + 5 titles
        self.assertEqual(pool.call_args.kwargs["max_workers"], 2)
        self.assertEqual(len(results), 30)
        self.assertEqual(report["failed"], 0)
        self.assertIn("Output only valid JSON", fake.prompts[0])

    def test_openai_path_keeps_small_chunks(self):
        self.assertEqual(get_RSS.OPENAI_ANALYSIS_CHUNK_SIZE, 10)
        self.assertEqual(get_RSS.AI_ANALYSIS_WORKERS, 5)

    def test_analysis_runs_with_codex_and_no_openai_key(self):
        items = [{"paper_id": "p1", "title": "T1", "translation": None}]
        with patch.object(get_RSS, "find_codex_executable", return_value=FAKE_CODEX), \
                patch.object(get_RSS, "batch_analyze_papers", return_value={"T1": {"zh": "x"}}) as batch, \
                patch.object(get_RSS, "save_db_translations", return_value=1) as save:
            saved = get_RSS.analyze_database_items("db", items, config={"OPENAI_API_KEY": None})
        self.assertEqual(saved, 1)
        key = batch.call_args.args[1]
        self.assertTrue(get_RSS.is_codex_backend(key))
        self.assertEqual(batch.call_args.kwargs["model"], "gpt-6-luna")
        save.assert_called_once_with("db", {"p1": {"zh": "x"}})

    def test_analysis_skipped_without_any_backend(self):
        items = [{"paper_id": "p1", "title": "T1", "translation": None}]
        out = io.StringIO()
        with patch.object(get_RSS, "batch_analyze_papers") as batch, contextlib.redirect_stdout(out):
            self.assertEqual(get_RSS.analyze_database_items("db", items, config={}), 0)
        batch.assert_not_called()
        self.assertIn("no AI backend available", out.getvalue())

    def test_reanalysis_without_backend_reports_reason(self):
        with patch.object(get_RSS, "get_config", return_value={}):
            result = get_RSS.run_reanalysis_flow()
        self.assertEqual(result["status"], "error")
        self.assertIn("No AI backend available", result["message"])


class CliTests(unittest.TestCase):
    def test_run_note_mentions_subscription_quota_for_codex(self):
        server = cli._legacy("server")
        with patch.object(cli, "probe_port", return_value="free"), \
                patch.object(server, "run_server", return_value=0), \
                patch.object(get_RSS, "get_config", return_value={}), \
                patch.object(get_RSS, "find_codex_executable", return_value=FAKE_CODEX), \
                patch.object(get_RSS, "run_rss_flow", return_value={"published": True}):
            code, output = run_cli("run", "--port", "18031", "--no-browser")
        self.assertEqual(code, 0)
        self.assertIn("ChatGPT subscription quota", output)
        self.assertNotIn("costs money", output)

    def test_reanalyze_dry_run_uses_codex_without_key(self):
        with tempfile.TemporaryDirectory() as temp:
            database = os.path.join(temp, "paper_feed.sqlite3")
            Path(database).write_bytes(b"")
            with patch.dict(os.environ, {"PAPER_FEED_DB": database}), \
                    patch.object(get_RSS, "get_config", return_value={}), \
                    patch.object(get_RSS, "find_codex_executable", return_value=FAKE_CODEX), \
                    patch("paper_feed.exporter.database_items", return_value=[{"paper_id": "p", "title": "T"}]):
                code, output = run_cli("reanalyze", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("the AI backend was not called", output)
        self.assertNotIn("not configured", output)


class DoctorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "journals.dat").write_text("https://example.test/rss\n", encoding="utf-8")
        (self.root / "keywords.dat").write_text("consumer\n", encoding="utf-8")
        (self.root / "web").mkdir()
        for name in ("index.html", "app.js"):
            (self.root / "web" / name).write_text("x", encoding="utf-8")
        env = {key: value for key, value in os.environ.items() if key not in cli.CONFIG_KEYS and key != "PAPER_FEED_DB"}
        self.env = patch.dict(os.environ, env, clear=True)
        self.env.start()
        self.probe = patch.object(cli, "probe_port", return_value="free")
        self.probe.start()

    def tearDown(self):
        self.probe.stop()
        self.env.stop()
        self.temp.cleanup()

    def doctor(self):
        return run_cli("doctor", "--root", str(self.root), "--ascii")

    def test_codex_found_reports_version_and_backend(self):
        with patch.object(cli, "find_codex_executable", return_value=FAKE_CODEX), \
                patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(
                    [FAKE_CODEX, "--version"], 0, b"codex-cli 0.160.0\n", b"")) as run:
            code, output = self.doctor()
        self.assertEqual(code, 0, output)
        self.assertIn("codex-cli 0.160.0", output)
        self.assertIn("codex (Codex CLI, model gpt-6-luna", output)
        self.assertIn("not needed while the Codex CLI backend is used", output)
        self.assertEqual(run.call_args.args[0], [FAKE_CODEX, "--version"])
        self.assertLessEqual(run.call_args.kwargs["timeout"], 30)

    def test_missing_codex_is_a_warning_not_a_failure(self):
        code, output = self.doctor()
        self.assertEqual(code, 0, output)
        self.assertNotIn("[FAIL]", output)
        self.assertRegex(output, r"\[WARN\]\s+Codex CLI\s+not found")
        self.assertRegex(output, r"\[WARN\]\s+AI backend\s+none")

    def test_missing_codex_with_key_reports_openai_fallback(self):
        with patch.dict(os.environ, {"OPENAI_API_KEY": "sk-doctor-secret"}):
            code, output = self.doctor()
        self.assertEqual(code, 0, output)
        self.assertIn("openai (OpenAI-compatible API) (fallback", output)
        self.assertNotIn("sk-doctor-secret", output)

    def test_codex_version_timeout_is_a_warning(self):
        with patch.object(cli, "find_codex_executable", return_value=FAKE_CODEX), \
                patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired("codex", 15)):
            code, output = self.doctor()
        self.assertEqual(code, 0, output)
        self.assertIn("timed out", output)
        self.assertRegex(output, r"\[WARN\]\s+AI backend")


if __name__ == "__main__":
    unittest.main()
