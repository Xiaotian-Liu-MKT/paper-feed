from pathlib import Path


LAUNCHER = Path(__file__).resolve().parents[1] / "run_web.bat"
ROOT = LAUNCHER.parent


def launcher_text():
    return LAUNCHER.read_text(encoding="utf-8").replace("\r\n", "\n")


SH_LAUNCHER = ROOT / "run_web.sh"


def sh_text():
    return SH_LAUNCHER.read_text(encoding="utf-8")


def test_launcher_uses_only_project_venv_and_defaults_to_refresh():
    text = launcher_text()
    assert 'set "PYTHON=%~dp0.venv\\Scripts\\python.exe"' in text
    assert 'set "MODE=refresh"' in text
    assert '"%PYTHON%" -m paper_feed %COMMAND% --port 8000' in text
    assert 'python get_RSS.py' not in text.lower()
    assert 'server.py' not in text


def test_launcher_validates_zero_or_one_supported_argument():
    text = launcher_text()
    assert 'if not "%~3"=="" goto :usage' in text
    assert 'if not "%~2"=="" goto :usage' in text
    assert 'if /I "%~1"=="refresh"' in text
    assert 'if /I "%~1"=="start"' in text
    assert 'else (\n    goto :usage' in text
    assert ':usage\n' in text


def test_launcher_reports_missing_virtual_environment_before_work():
    text = launcher_text()
    venv_check = text.index('if not exist "%PYTHON%" goto :missing_venv')
    cli = text.index('"%PYTHON%" -m paper_feed %COMMAND%')
    assert venv_check < cli
    assert ':missing_venv\n' in text
    assert 'py -m venv .venv' in text


def test_launcher_maps_modes_to_cli_commands_and_documents_start():
    text = launcher_text()
    # refresh (default) -> `run` (refresh, then serve/open); start -> `start` (no network).
    assert 'set "COMMAND=run"' in text
    assert 'if /I "%MODE%"=="start" set "COMMAND=start"' in text
    assert text.count('-m paper_feed %COMMAND%') == 1
    assert 'run_web.bat start' not in text  # usage is generated from %~nx0
    assert 'start     ^(start/open existing local data without refreshing RSS^)' in text
    assert 'may access RSS networks, call OpenAI' in text


def test_launcher_delegates_existing_service_detection_to_cli():
    """The CLI probes /api/interactions and only opens an already running Paper Feed."""
    from paper_feed import cli
    import inspect
    source = inspect.getsource(cli.probe_port) + inspect.getsource(cli._start)
    assert "/api/interactions" in source
    assert '("favorites", "archived", "hidden")' in source
    assert 'state == "paper_feed"' in source and 'state == "busy"' in source
    assert "open_browser" in source


def test_posix_launcher_mirrors_batch_launcher():
    text = sh_text()
    assert 'PYTHON="$ROOT/.venv/bin/python"' in text
    assert 'PORT="${PAPER_FEED_PORT:-8000}"' in text
    assert 'exec "$PYTHON" -m paper_feed "$COMMAND" --port "$PORT"' in text
    assert 'COMMAND=run' in text and 'COMMAND=start' in text
    assert 'get_RSS.py' not in text and 'server.py' not in text


def test_startup_documentation_matches_launcher_and_sqlite_architecture():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    context = (ROOT / "DEV_CONTEXT.md").read_text(encoding="utf-8")
    for document in (readme, context):
        assert "run_web.bat refresh" in document
        assert "run_web.bat start" in document
        assert "get_RSS.py" in document
        assert "SQLite" in document
        assert "paper_id" in document
        assert "127.0.0.1:8000" in document
        assert "OpenAI" in document
    assert "后台 job" in readme
    assert "后台 job" in context
