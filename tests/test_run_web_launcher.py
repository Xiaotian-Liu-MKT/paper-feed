from pathlib import Path


LAUNCHER = Path(__file__).resolve().parents[1] / "run_web.bat"
ROOT = LAUNCHER.parent


def launcher_text():
    return LAUNCHER.read_text(encoding="utf-8").replace("\r\n", "\n")


def test_launcher_uses_only_project_venv_and_defaults_to_refresh():
    text = launcher_text()
    assert 'set "PYTHON=%~dp0.venv\\Scripts\\python.exe"' in text
    assert 'set "MODE=refresh"' in text
    assert '"%PYTHON%" "%~dp0server.py"' in text
    assert 'python get_RSS.py' not in text.lower()


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
    refresh = text.index('"%PYTHON%" "%~dp0get_RSS.py"')
    server = text.index('"%PYTHON%" "%~dp0server.py"')
    assert venv_check < refresh < server
    assert ':missing_venv\n' in text
    assert 'py -m venv .venv' in text


def test_launcher_runs_rss_only_in_refresh_branch_and_documents_start():
    text = launcher_text()
    refresh_branch = text[text.index('if /I "%MODE%"=="refresh"'):text.index('for /f %%i')]
    assert '"%PYTHON%" "%~dp0get_RSS.py"' in refresh_branch
    assert text.count('"%PYTHON%" "%~dp0get_RSS.py"') == 1
    assert 'run_web.bat start' not in text  # usage is generated from %~nx0
    assert 'start     ^(start/open existing local data without refreshing RSS^)' in text


def test_launcher_checks_existing_service_and_opens_loopback_url():
    text = launcher_text()
    assert 'Get-NetTCPConnection -LocalPort 8000 -State Listen' in text
    assert "http://127.0.0.1:8000/api/interactions" in text
    assert "ConvertFrom-Json -ErrorAction Stop" in text
    assert "$required = 'favorites','archived','hidden'" in text
    assert "$missing.Count -eq 0 -and $invalid.Count -eq 0" in text
    assert "<title>Paper Feed</title>" not in text
    assert "-like '*Paper Feed*'" not in text
    assert 'EXISTING_PAPER_FEED=1' in text
    assert 'http://127.0.0.1:8000/?t=%CACHE_BUSTER%' in text
    assert 'Port 8000 is already in use by PID(s):' in text


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
