"""Keep the test suite offline with respect to the Codex CLI backend.

AI_BACKEND defaults to "codex", and a developer machine usually has the Codex
CLI installed.  Test modules that touch AI gating import ``setUpModule`` /
``tearDownModule`` from here (both unittest and pytest honour them), so Codex
discovery returns None and the suite behaves like a machine without Codex
(e.g. GitHub Actions): "no OPENAI_API_KEY -> AI skipped" stays meaningful and
a real ``codex exec`` is never spawned.  Tests that exercise the Codex path
patch ``find_codex_executable`` / ``subprocess.run`` themselves.
"""
from unittest import mock

import get_RSS
from paper_feed import cli

_ACTIVE = []
# Unpatched discovery functions, for tests of the discovery logic itself.
REAL_FIND_CODEX = get_RSS.find_codex_executable
REAL_CLI_FIND_CODEX = cli.find_codex_executable


def setUpModule():
    for module in (get_RSS, cli):
        patcher = mock.patch.object(module, "find_codex_executable", return_value=None)
        patcher.start()
        _ACTIVE.append(patcher)


def tearDownModule():
    while _ACTIVE:
        _ACTIVE.pop().stop()
