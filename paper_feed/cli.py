"""Unified Paper Feed command line: ``python -m paper_feed <command>``.

统一命令行入口。``get_RSS.py`` / ``server.py`` 与 ``python -m paper_feed.publish_guard``
仍可直接运行，它们只是委托到这里的兼容外壳。

The heavy root modules (``get_RSS``, ``server``) are imported lazily inside the
command handlers: they import ``paper_feed`` themselves, and importing them at
module level would make ``--help`` depend on feedparser/openai being installed.
"""
import argparse
import importlib
import json
import os
import socket
import sqlite3
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DEFAULT_PORT = 8000
DEFAULT_HOST = "127.0.0.1"
MIN_PYTHON = (3, 10)          # publish_guard uses `X | None` annotations
RECOMMENDED_PYTHON = (3, 11)  # README / GitHub Actions
# Mirrors get_RSS.CONFIG_KEYS / CONFIG_DEFAULTS (kept here so `doctor` works even
# when get_RSS cannot be imported because a dependency is missing).
CONFIG_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_PROXY", "OPENAI_MODEL")
CONFIG_DEFAULTS = {"OPENAI_MODEL": "gpt-4o-mini"}
REQUIRED_MODULES = ("feedparser", "rfeed", "requests")
AI_MODULES = ("openai", "httpx")
REQUIRED_WEB_ASSETS = ("index.html", "app.js")

EXIT_OK = 0
EXIT_FAILURE = 1

_ORIGINAL_STDOUT_ENCODING = None


# --- small helpers -------------------------------------------------------------

def _configure_stdio():
    """Avoid UnicodeEncodeError on legacy Windows consoles (GBK/cp1252)."""
    global _ORIGINAL_STDOUT_ENCODING
    if _ORIGINAL_STDOUT_ENCODING is None:
        _ORIGINAL_STDOUT_ENCODING = getattr(sys.stdout, "encoding", None) or "ascii"
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def _legacy(name):
    """Import a root-level module (get_RSS / server) from the project directory."""
    if name in sys.modules:
        return sys.modules[name]
    root = str(PROJECT_DIR)
    if root not in sys.path:
        sys.path.insert(0, root)
    return importlib.import_module(name)


def _load_rss():
    try:
        return _legacy("get_RSS")
    except ImportError as error:
        missing = getattr(error, "name", None) or str(error)
        print(f"Error: a dependency is missing ({missing}). 缺少依赖，请先安装：", file=sys.stderr)
        print("  Windows: .venv\\Scripts\\python.exe -m pip install -r requirements.txt", file=sys.stderr)
        print("  macOS/Linux: .venv/bin/python -m pip install -r requirements.txt", file=sys.stderr)
        print("Run `python -m paper_feed doctor` for a full check.", file=sys.stderr)
        raise SystemExit(EXIT_FAILURE)


def _database_path():
    return os.environ.get("PAPER_FEED_DB") or str(PROJECT_DIR / "data" / "paper_feed.sqlite3")


def _port_type(value):
    try:
        port = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"invalid port: {value!r}")
    if not 0 < port < 65536:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535 / 端口须在 1-65535 之间")
    return port


def resolve_port(cli_port=None):
    """--port > PAPER_FEED_PORT > 8000 (same rule as server.resolve_port)."""
    if cli_port is not None:
        return int(cli_port)
    env_port = os.environ.get("PAPER_FEED_PORT", "").strip()
    if env_port:
        try:
            port = int(env_port)
            if 0 < port < 65536:
                return port
        except ValueError:
            pass
    return DEFAULT_PORT


def _probe_host(host):
    return DEFAULT_HOST if host in {None, "", "0.0.0.0", "::", "localhost"} else host


def probe_port(port, host=DEFAULT_HOST, timeout=2.0):
    """Return "free", "paper_feed" (a Paper Feed server answers) or "busy"."""
    target = _probe_host(host)
    try:
        with socket.create_connection((target, port), timeout=1.0):
            pass
    except OSError:
        return "free"
    try:
        with urllib.request.urlopen(f"http://{target}:{port}/api/interactions", timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return "busy"
    keys = ("favorites", "archived", "hidden")
    if isinstance(data, dict) and all(isinstance(data.get(key), list) for key in keys):
        return "paper_feed"
    return "busy"


def open_browser(url):
    """Open *url* (with a cache buster) without blocking the caller."""
    target = f"{url.rstrip('/')}/?t={int(time.time() * 1000)}"
    print(f"Opening {target}")
    threading.Thread(target=webbrowser.open, args=(target,), daemon=True).start()
    return target


def _stdin_is_interactive():
    try:
        return sys.stdin is not None and sys.stdin.isatty()
    except (AttributeError, ValueError):
        return False


def confirm(question, assume_yes=False):
    if assume_yes:
        return True
    if not _stdin_is_interactive():
        print("Not an interactive terminal; re-run with --yes to proceed. 非交互环境，请加 --yes 确认执行。")
        return False
    try:
        answer = input(f"{question} [y/N] ")
    except EOFError:
        return False
    return answer.strip().lower() in {"y", "yes", "是", "确定"}


def refresh_exit_code(outcome):
    """0 published (possibly with failed sources), 1 all sources failed, 2 config error."""
    outcome = outcome or {}
    if outcome.get("published"):
        failed = outcome.get("failed_sources") or []
        if failed:
            print(f"Finished with {len(failed)} failed source(s); available data was published.")
        return 0
    if outcome.get("config_error"):
        return 2
    return 1


def _ai_key_configured(rss):
    try:
        return bool(rss.get_config().get("OPENAI_API_KEY"))
    except Exception:
        return False


# --- command handlers ------------------------------------------------------------

def cmd_refresh(args):
    rss = _load_rss()
    return refresh_exit_code(rss.run_rss_flow())


def cmd_serve(args):
    server = _legacy("server")
    port = server.resolve_port(args.port)
    on_ready = open_browser if args.open else None
    return server.run_server(port, host=args.host, on_ready=on_ready)


def _start(args, refresh):
    server = _legacy("server")
    port = server.resolve_port(args.port)
    url = server.browser_url(args.host, port)
    state = probe_port(port, args.host)
    if state == "busy":
        print(f"Error: port {port} is already in use by another program. 端口 {port} 已被其他程序占用。")
        print(f"  Stop that program, or choose another port: python -m paper_feed {args.command} --port 8001")
        print("  (or set the PAPER_FEED_PORT environment variable / 或设置 PAPER_FEED_PORT 环境变量)")
        return EXIT_FAILURE
    if refresh:
        rss = _load_rss()
        print("Refresh accesses RSS networks and rewrites generated files. 刷新会联网抓取 RSS 并改写导出文件。")
        if _ai_key_configured(rss):
            print("NOTE: OPENAI_API_KEY is configured - new papers will be translated/classified with OpenAI, "
                  "which costs money. 已配置 OpenAI 密钥：新论文的标题分析会产生 API 费用。"
                  " Use `python -m paper_feed start` to skip the refresh.")
        print("Running RSS refresh before opening Paper Feed...")
        code = refresh_exit_code(rss.run_rss_flow())
        if code != 0:
            print()
            print("Warning: Refresh did not publish new data (see the messages above). 刷新未发布新数据。")
            print("Exit code 1 = every RSS source failed; 2 = journals.dat or keywords.dat is empty.")
            print("Opening Paper Feed with the existing local data instead. 将使用现有本地数据打开。")
            print()
    if state == "paper_feed":
        print(f"Paper Feed is already running on port {port}; opening it. Paper Feed 已在运行，直接打开。")
        if not args.no_browser:
            open_browser(url)
        return EXIT_OK
    return server.run_server(port, host=args.host, on_ready=None if args.no_browser else open_browser)


def cmd_start(args):
    return _start(args, refresh=False)


def cmd_run(args):
    return _start(args, refresh=True)


def _missing_database_notice():
    print(f"Database not found yet: {_database_path()} (nothing to count). "
          "数据库尚不存在；首次 refresh/start 时会创建。")
    return EXIT_OK


def cmd_reanalyze(args):
    rss = _load_rss()
    if args.dry_run and not os.path.exists(_database_path()):
        return _missing_database_notice()
    from .exporter import database_items
    database = _database_path() if args.dry_run else rss._database_path()
    stale = rss.stale_analysis_items(database_items(database))
    titles = len(dict.fromkeys(item["title"] for item in stale))
    print(f"{len(stale)} paper(s) need title analysis ({titles} unique title(s)). "
          f"{len(stale)} 篇论文缺少或需要更新标题分析（{titles} 个不同标题）。")
    if not stale:
        print("Nothing to do. 无需处理。")
        return EXIT_OK
    config = rss.get_config()
    if not config.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not configured; AI analysis is unavailable. 未配置 OPENAI_API_KEY。")
        return EXIT_OK if args.dry_run else EXIT_FAILURE
    if args.dry_run:
        print("--dry-run: OpenAI was not called. 仅统计，未调用 OpenAI。")
        return EXIT_OK
    question = (f"Analyse {titles} title(s) with {rss.config_model(config)}? This calls OpenAI and costs money. "
                "将调用 OpenAI 并产生费用，继续？")
    if not confirm(question, args.yes):
        print("Cancelled. 已取消。")
        return EXIT_FAILURE
    result = rss.run_reanalysis_flow() or {}
    print(result.get("message", ""))
    return EXIT_OK if result.get("status") == "ok" and not result.get("failed") else EXIT_FAILURE


def cmd_summarize_favorites(args):
    rss = _load_rss()
    if args.dry_run and not os.path.exists(_database_path()):
        return _missing_database_notice()
    from .service import PaperFeedService
    service = PaperFeedService(PROJECT_DIR, os.environ.get("PAPER_FEED_DB"))
    favorites = service.favorite_legacy_ids()
    legacy_ids = [legacy_id for _, legacy_id in favorites if legacy_id]
    pending = rss.pending_summary_items(service.database, legacy_ids)
    print(f"{len(favorites)} favorite(s); {len(pending)} still need an AI summary. "
          f"收藏 {len(favorites)} 篇，其中 {len(pending)} 篇尚无 AI 总结。")
    if not pending:
        print("Nothing to do. 无需处理。")
        return EXIT_OK
    config = rss.get_config()
    if not config.get("OPENAI_API_KEY"):
        print("OPENAI_API_KEY is not configured; AI summaries are unavailable. 未配置 OPENAI_API_KEY。")
        return EXIT_OK if args.dry_run else EXIT_FAILURE
    if args.dry_run:
        print("--dry-run: OpenAI was not called. 仅统计，未调用 OpenAI。")
        return EXIT_OK
    question = (f"Summarize {len(pending)} favorite(s) with {rss.config_model(config)}? "
                "This calls OpenAI and costs money. 将调用 OpenAI 并产生费用，继续？")
    if not confirm(question, args.yes):
        print("Cancelled. 已取消。")
        return EXIT_FAILURE
    result = rss.summarize_specific_papers(legacy_ids) or {}
    print(result.get("message", ""))
    return EXIT_OK if result.get("status") == "ok" and not result.get("failed") else EXIT_FAILURE


def _config_lines(lines):
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith("#")]


def _env_list(name):
    content = os.environ.get(name, "")
    if not content.strip():
        return None
    return _config_lines(content.split("\n" if "\n" in content else ";"))


def _active_keywords(root=PROJECT_DIR):
    """(rules, source description) using the same precedence as get_RSS.load_config."""
    override = _env_list("RSS_KEYWORDS")
    if override is not None:
        return override, "RSS_KEYWORDS environment variable (overrides keywords.dat)"
    path = Path(root) / "keywords.dat"
    if not path.exists():
        return [], f"{path} (missing)"
    return _config_lines(path.read_text(encoding="utf-8").splitlines()), str(path)


def cmd_keywords_show(args):
    rss = _load_rss()
    rules, source = _active_keywords()
    print(f"Source / 来源: {source}")
    print(f"Rules / 规则: {len(rules)}")
    for rule in rules:
        parsed = rss.parse_keyword_rule(rule)
        note = "" if parsed["include"] else "   <- only exclusions, matches nothing / 仅排除词，不会命中"
        print(f"  {rule}{note}")
    if not rules:
        print("No keyword rules: `refresh` will exit with code 2. 没有关键词规则，refresh 会以退出码 2 结束。")
    return EXIT_OK


def cmd_keywords_preview(args):
    server = _legacy("server")
    if args.file:
        text = Path(args.file).read_text(encoding="utf-8")
        source = args.file
    elif args.text is not None:
        text = args.text
        source = "--text"
    else:
        rules, source = _active_keywords()
        text = "\n".join(rules)
    result = server.keyword_preview(text)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return EXIT_OK
    total = result.get("total_papers", 0)
    matched = result.get("matched", 0)
    share = f" ({matched / total:.1%})" if total else ""
    print(f"Rules from / 规则来源: {source}")
    print(f"Papers in SQLite / 库中论文: {total}")
    print(f"Matched / 命中: {matched}{share}")
    if result.get("terms"):
        print("Term hits / 各词命中:")
        for term in result["terms"]:
            print(f"  {term['count']:>6}  {term['term']}")
    if result.get("samples"):
        print("Samples / 命中示例:")
        for sample in result["samples"]:
            print(f"  - {sample['title']}")
    return EXIT_OK


def cmd_backup(args):
    from .backup import backup_database
    try:
        result = backup_database(args.database, args.out)
    except FileNotFoundError as error:
        print(f"Error: {error}", file=sys.stderr)
        return EXIT_FAILURE
    print(json.dumps(result, indent=2))
    return EXIT_OK if result["integrity"] == "ok" else EXIT_FAILURE


def cmd_import_legacy(args):
    from .importer import LegacyImporter
    print(json.dumps(LegacyImporter(args.root, args.database).run(args.dry_run), indent=2))
    return EXIT_OK


def cmd_publish_guard(args):
    from . import publish_guard
    return publish_guard.run(args)


# --- doctor ------------------------------------------------------------------------

OK, WARN, FAIL, INFO = "ok", "warn", "fail", "info"
UNICODE_MARKS = {OK: "✓", WARN: "!", FAIL: "✗", INFO: "·"}
ASCII_MARKS = {OK: "[OK]  ", WARN: "[WARN]", FAIL: "[FAIL]", INFO: "[INFO]"}


def _can_encode(text, encoding):
    try:
        text.encode(encoding or "ascii")
        return True
    except (LookupError, UnicodeEncodeError):
        return False


def _usable(value):
    """Same rule as get_RSS.is_usable_config_value: empty/"your-..." placeholders are unset."""
    if value is None:
        return False
    if not isinstance(value, str):
        return True
    text = value.strip()
    return bool(text) and not text.lower().startswith("your-")


def _display_value(key, value):
    """Never print secrets: keys/proxies are only reported as set."""
    if key == "OPENAI_MODEL":
        return str(value)
    if key == "OPENAI_BASE_URL":
        from urllib.parse import urlparse
        parsed = urlparse(str(value))
        return f"{parsed.scheme}://{parsed.hostname}" if parsed.scheme and parsed.hostname else "set / 已设置"
    return "set / 已设置"


def resolve_config_sources(root):
    """Return ({key: (source, display)}, problems) following get_RSS.get_config precedence."""
    problems = []
    local = {}
    config_file = Path(root) / "config.json"
    if config_file.exists():
        try:
            loaded = json.loads(config_file.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                local = loaded
            else:
                problems.append("config.json is not a JSON object / config.json 不是 JSON 对象")
        except (OSError, ValueError) as error:
            problems.append(f"config.json cannot be read: {type(error).__name__} / config.json 无法解析")
    resolved = {}
    for key in CONFIG_KEYS:
        env_value, file_value = os.environ.get(key), local.get(key)
        if _usable(env_value):
            resolved[key] = ("env", _display_value(key, env_value.strip()))
        elif _usable(file_value):
            resolved[key] = ("config.json", _display_value(key, file_value))
        elif key in CONFIG_DEFAULTS:
            resolved[key] = ("default", CONFIG_DEFAULTS[key])
        else:
            placeholder = (isinstance(file_value, str) and file_value.strip() != "") or \
                          (isinstance(env_value, str) and env_value.strip() != "")
            resolved[key] = ("unset", "placeholder ignored / 占位值已忽略" if placeholder else "not set / 未设置")
    return resolved, problems


def run_doctor(root=PROJECT_DIR, port=None, database=None):
    """Return a list of (status, label, detail) checks."""
    root = Path(root)
    checks = []
    add = lambda status, label, detail="": checks.append((status, label, detail))

    version = ".".join(map(str, sys.version_info[:3]))
    if sys.version_info >= RECOMMENDED_PYTHON:
        add(OK, "Python", f"{version} ({sys.executable})")
    elif sys.version_info >= MIN_PYTHON:
        add(WARN, "Python", f"{version}; Python {'.'.join(map(str, RECOMMENDED_PYTHON))}+ is recommended")
    else:
        add(FAIL, "Python", f"{version}; Paper Feed needs >= {'.'.join(map(str, MIN_PYTHON))}")

    from importlib import metadata
    def dist_version(name):
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            return "?"

    for name in REQUIRED_MODULES + AI_MODULES:
        try:
            importlib.import_module(name)
            add(OK, f"import {name}", dist_version(name))
        except Exception as error:
            status = FAIL if name in REQUIRED_MODULES else WARN
            impact = "RSS refresh cannot run" if name in REQUIRED_MODULES else "AI features unavailable"
            add(status, f"import {name}",
                f"{type(error).__name__}: {error} - {impact}; pip install -r requirements.txt")

    config, problems = resolve_config_sources(root)
    try:
        import httpx
        import inspect
        supports_proxy = "proxy" in inspect.signature(httpx.Client.__init__).parameters
        if supports_proxy:
            add(OK, "httpx proxy compatibility", f"httpx {httpx.__version__} accepts proxy=")
        else:
            status = FAIL if config["OPENAI_PROXY"][0] != "unset" else WARN
            add(status, "httpx proxy compatibility",
                f"httpx {httpx.__version__} has no proxy= argument; OPENAI_PROXY will not work "
                "(requirements: httpx>=0.27,<1)")
    except ImportError:
        pass

    for problem in problems:
        add(WARN, "config.json", problem)
    for key in CONFIG_KEYS:
        source, display = config[key]
        if key == "OPENAI_API_KEY" and source == "unset":
            add(WARN, key, f"{display} - AI translation/classification/summaries are skipped")
        else:
            add(OK if source != "unset" else INFO, key, f"{display} (source: {source})")

    for filename, env_name, what in (("journals.dat", "RSS_JOURNALS", "journal feed"),
                                     ("keywords.dat", "RSS_KEYWORDS", "keyword rule")):
        override = _env_list(env_name)
        path = root / filename
        if override is not None:
            status = OK if override else FAIL
            add(status, filename, f"overridden by {env_name} ({len(override)} {what}(s)); "
                                  f"unset it to use the file / 环境变量覆盖了文件")
            continue
        entries = _config_lines(path.read_text(encoding="utf-8").splitlines()) if path.exists() else []
        if entries:
            add(OK, filename, f"{len(entries)} {what}(s)")
        else:
            state = "empty" if path.exists() else "missing"
            add(FAIL, filename, f"{state} - `refresh` exits with code 2 / 文件{'为空' if path.exists() else '缺失'}")

    db_path = Path(database or os.environ.get("PAPER_FEED_DB") or root / "data" / "paper_feed.sqlite3")
    if not db_path.exists():
        add(WARN, "SQLite database", f"{db_path} not found - created (or imported from legacy exports) "
                                     "on the first refresh/start / 首次运行时自动创建")
    else:
        try:
            conn = sqlite3.connect(str(db_path), timeout=5)
            try:
                integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                papers = conn.execute("SELECT count(*) FROM papers").fetchone()[0] if "papers" in tables else 0
                states = dict(conn.execute("SELECT state, count(*) FROM paper_review_state GROUP BY state")
                              .fetchall()) if "paper_review_state" in tables else {}
            finally:
                conn.close()
            if integrity == "ok":
                add(OK, "SQLite database", f"{db_path} (integrity_check: ok)")
            else:
                add(FAIL, "SQLite database", f"{db_path} integrity_check: {integrity}; restore a backup")
            breakdown = ", ".join(f"{name} {states.get(name, 0)}" for name in ("inbox", "favorite", "archived", "hidden"))
            add(INFO, "Papers / 论文", f"{papers} total ({breakdown})")
        except sqlite3.Error as error:
            add(FAIL, "SQLite database", f"{db_path}: {type(error).__name__}: {error}")

    env_port = os.environ.get("PAPER_FEED_PORT", "").strip()
    if port is None and env_port and resolve_port(None) == DEFAULT_PORT and env_port != str(DEFAULT_PORT):
        add(WARN, "PAPER_FEED_PORT", f"invalid value {env_port!r}; {DEFAULT_PORT} is used")
    chosen_port = resolve_port(port)
    state = probe_port(chosen_port)
    if state == "free":
        add(OK, f"Port {chosen_port}", "free / 可用")
    elif state == "paper_feed":
        add(OK, f"Port {chosen_port}", "Paper Feed is already running here / Paper Feed 已在运行")
    else:
        add(WARN, f"Port {chosen_port}", "used by another program; use --port or PAPER_FEED_PORT / 被其他程序占用")

    web = root / "web"
    missing = [name for name in REQUIRED_WEB_ASSETS if not (web / name).is_file()]
    if missing:
        add(FAIL, "web/ assets", f"missing {', '.join(missing)} in {web}")
    else:
        add(OK, "web/ assets", f"{web}")
    return checks


def cmd_doctor(args):
    checks = run_doctor(args.root, args.port, args.database)
    marks = UNICODE_MARKS
    if args.ascii or not _can_encode("".join(UNICODE_MARKS.values()), _ORIGINAL_STDOUT_ENCODING
                                     or getattr(sys.stdout, "encoding", None)):
        marks = ASCII_MARKS
    width = max(len(label) for _, label, _ in checks)
    print(f"Paper Feed doctor - {Path(args.root).resolve()}")
    for status, label, detail in checks:
        print(f" {marks[status]} {label.ljust(width)}  {detail}")
    blocking = sum(1 for status, _, _ in checks if status == FAIL)
    warnings = sum(1 for status, _, _ in checks if status == WARN)
    print()
    if blocking:
        print(f"{blocking} blocking problem(s), {warnings} warning(s). 发现 {blocking} 个阻塞问题。")
        return EXIT_FAILURE
    print(f"No blocking problems ({warnings} warning(s)). 未发现阻塞问题。")
    return EXIT_OK


# --- parser ------------------------------------------------------------------------

class _Formatter(argparse.RawDescriptionHelpFormatter):
    def __init__(self, prog):
        super().__init__(prog, max_help_position=32)


QUICK_START = """quick start / 快速开始:
  python -m paper_feed doctor      check the installation / 环境自检
  python -m paper_feed start       open existing data, no network / 打开现有数据（不联网）
  python -m paper_feed run         refresh RSS, then open / 先刷新再打开
"""


def _add_server_options(parser, open_flag):
    parser.add_argument("--port", type=_port_type, default=None,
                        help=f"TCP port (default: PAPER_FEED_PORT, else {DEFAULT_PORT}) / 端口")
    parser.add_argument("--host", default=DEFAULT_HOST,
                        help=f"interface to bind (default: {DEFAULT_HOST}; the API has no authentication, "
                             "keep it on loopback) / 监听地址，请保持本机地址")
    if open_flag:
        parser.add_argument("--open", action="store_true",
                            help="open the browser once the server is listening / 启动后打开浏览器")
    else:
        parser.add_argument("--no-browser", action="store_true",
                            help="do not open a browser window / 不自动打开浏览器")


def _add_ai_options(parser):
    parser.add_argument("--yes", "-y", action="store_true",
                        help="do not ask for confirmation (required when not on a terminal) / 跳过确认")
    parser.add_argument("--dry-run", action="store_true",
                        help="only print how many papers would be processed; never calls OpenAI / 仅统计数量")


def _add_import_options(parser):
    parser.add_argument("--root", default=str(PROJECT_DIR),
                        help="project directory containing legacy files (default: this project) / 旧文件所在目录")
    parser.add_argument("--database",
                        help="SQLite database path (default: <root>/data/paper_feed.sqlite3) / 数据库路径")
    parser.add_argument("--dry-run", action="store_true",
                        help="import into a temporary shadow database and only report counts / 只演练不写入")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="python -m paper_feed", formatter_class=_Formatter,
        description="Paper Feed - academic RSS filter with a local review UI.\n"
                    "Paper Feed 学术论文 RSS 订阅与本地筛选工具。",
        epilog=QUICK_START + "\nRun `python -m paper_feed <command> --help` for details. "
               "查看某个命令的说明：python -m paper_feed <命令> --help\n"
               "Legacy entry points still work: python get_RSS.py (= refresh), python server.py (= serve).")
    commands = parser.add_subparsers(dest="command", metavar="<command>", title="commands / 命令")

    def add(name, handler, help_text, description, epilog=None):
        sub = commands.add_parser(name, help=help_text, description=description, epilog=epilog,
                                  formatter_class=_Formatter)
        sub.set_defaults(handler=handler)
        return sub

    sub = add("start", cmd_start, "open existing local data without refreshing / 打开现有数据（不联网）",
              "Start the local server on existing data and open the browser. No RSS fetch, no OpenAI.\n"
              "If Paper Feed is already running on the port, just open it.\n"
              "使用现有本地数据启动服务并打开浏览器；不联网、不调用 OpenAI。若已在运行则直接打开。")
    _add_server_options(sub, open_flag=False)

    sub = add("run", cmd_run, "refresh RSS, then start / 先刷新 RSS 再打开",
              "Refresh RSS (network; OpenAI if a key is configured, which costs money), then start the\n"
              "server and open the browser. A failed refresh still opens the existing data.\n"
              "先刷新 RSS（联网；若配置了 OpenAI 密钥会产生费用），再启动并打开浏览器；刷新失败时仍打开现有数据。")
    _add_server_options(sub, open_flag=False)

    sub = add("serve", cmd_serve, "run the local web server / 仅启动本地 Web 服务",
              "Serve the web UI and local API (no authentication - do not expose it to a network).\n"
              "启动本地 Web 界面与 API（无鉴权，请勿暴露到公网）。",
              epilog="A port already in use is reported with a hint instead of a traceback.")
    _add_server_options(sub, open_flag=True)

    add("refresh", cmd_refresh, "fetch RSS, store in SQLite, regenerate exports / 抓取 RSS",
        "Fetch the feeds in journals.dat, keep entries matching keywords.dat, store them in SQLite,\n"
        "run AI title analysis when OPENAI_API_KEY is set, and regenerate filtered_feed.xml / web/feed.json.\n"
        "抓取 journals.dat 中的期刊，按 keywords.dat 过滤后入库，并重新生成导出文件。",
        epilog="exit codes / 退出码:\n"
               "  0  published (possibly with some failed sources) / 已发布\n"
               "  1  every RSS source failed, nothing published / 所有源抓取失败\n"
               "  2  journals.dat or keywords.dat is empty or missing / 配置为空")

    sub = add("reanalyze", cmd_reanalyze, "AI title analysis for unclassified/stale papers / 重新分析标题",
              "Translate and classify titles that have no analysis or an outdated classification version.\n"
              "Prints the count and asks before calling OpenAI.\n"
              "为未分类或分类版本过旧的论文调用 OpenAI 翻译/分类；执行前显示数量并确认。",
              epilog="exit codes: 0 done / nothing to do / dry run; 1 cancelled, no API key, or failures")
    _add_ai_options(sub)

    sub = add("summarize-favorites", cmd_summarize_favorites, "AI summaries for favorites / 为收藏生成 AI 总结",
              "Generate AI summaries for favorites that do not have one yet (uses the stored abstract when\n"
              "present, otherwise predicts from the title). Prints the count and asks first.\n"
              "为尚无 AI 总结的收藏生成总结；执行前显示数量并确认。",
              epilog="exit codes: 0 done / nothing to do / dry run; 1 cancelled, no API key, or failures")
    _add_ai_options(sub)

    keywords = add("keywords", None, "show or preview keyword rules / 查看或预览关键词规则",
                   "Inspect the keyword rules used by refresh (keywords.dat, or RSS_KEYWORDS when set).\n"
                   "查看 refresh 使用的关键词规则。")
    keyword_commands = keywords.add_subparsers(dest="keywords_command", metavar="<action>",
                                               title="actions / 操作")
    show = keyword_commands.add_parser("show", help="print the active rules / 显示当前规则",
                                       description="Print the active keyword rules and where they come from.\n"
                                                   "显示当前生效的关键词规则及其来源。",
                                       formatter_class=_Formatter)
    show.set_defaults(handler=cmd_keywords_show)
    preview = keyword_commands.add_parser(
        "preview", help="count stored papers matched by rules / 预览规则命中数",
        description="Evaluate keyword rules against the papers already stored in SQLite (no network).\n"
                    "Defaults to the active rules; use --text or --file to try a draft.\n"
                    "用库中已有论文预览规则命中情况（不联网）；默认使用当前规则。",
        epilog="rule syntax: one rule per line (OR); `a AND b`; exclude with `-term` or `NOT term`;\n"
               "\"quoted phrase\"; lines starting with # are comments.",
        formatter_class=_Formatter)
    source = preview.add_mutually_exclusive_group()
    source.add_argument("--text", help="rules to try (newline-separated) / 待预览的规则文本")
    source.add_argument("--file", help="read rules from this file / 从文件读取规则")
    preview.add_argument("--json", action="store_true", help="print the raw JSON result / 输出 JSON")
    preview.set_defaults(handler=cmd_keywords_preview)
    keywords.set_defaults(handler=lambda args: (keywords.print_help(), EXIT_OK)[1])

    sub = add("doctor", cmd_doctor, "check Python, dependencies, config, data and port / 环境自检",
              "Check the Python version, dependencies (incl. httpx proxy support), where each setting comes\n"
              "from (secrets are never printed), journals.dat / keywords.dat, the SQLite database, the port\n"
              "and web/ assets.\n"
              "检查 Python、依赖、配置来源（不显示密钥）、期刊/关键词文件、数据库、端口与前端文件。",
              epilog="exit codes: 0 no blocking problem (warnings allowed); 1 at least one blocking problem")
    sub.add_argument("--root", default=str(PROJECT_DIR),
                     help="project directory to check (default: this project) / 要检查的项目目录")
    sub.add_argument("--database", help="database to check (default: PAPER_FEED_DB or <root>/data/...) / 数据库路径")
    sub.add_argument("--port", type=_port_type, default=None,
                     help=f"port to check (default: PAPER_FEED_PORT, else {DEFAULT_PORT}) / 要检查的端口")
    sub.add_argument("--ascii", action="store_true", help="use ASCII status marks / 使用 ASCII 标记")

    sub = add("backup", cmd_backup, "back up the SQLite database / 备份数据库",
              "Write a consistent copy of the SQLite database with the sqlite3 backup API\n"
              "(safe while the server is running). 使用 sqlite3 备份 API 生成一致的数据库副本。")
    sub.add_argument("--out", metavar="DIR",
                     help="directory for the backup file (default: the database's data/ directory) / 输出目录")
    sub.add_argument("--database",
                     help="database to back up (default: PAPER_FEED_DB or data/paper_feed.sqlite3) / 数据库路径")

    sub = add("import-legacy", cmd_import_legacy, "import pre-SQLite JSON/XML files / 导入旧版数据",
              "One-way, idempotent import of legacy files (filtered_feed.xml, web/*.json) into SQLite.\n"
              "将旧版 XML/JSON 文件单向、幂等地导入 SQLite。")
    _add_import_options(sub)

    from . import publish_guard
    sub = add("publish-guard", cmd_publish_guard, "validate exports before publishing / 发布前校验导出",
              "Reject malformed, inconsistent, empty or unsafely shrunken exports before an automated\n"
              "publication (used by GitHub Actions). 发布前检查导出文件是否损坏、不一致或异常缩水。",
              epilog="exit codes: 0 passed; 1 rejected")
    publish_guard.configure_parser(sub)
    return parser


def main(argv=None):
    _configure_stdio()
    parser = build_parser()
    argv = sys.argv[1:] if argv is None else list(argv)
    if argv and argv[0] in {"--root", "--database", "--dry-run"}:
        # Bare `python -m paper_feed --dry-run` used to mean import-legacy.
        print("Hint: the legacy import is now an explicit command: "
              "python -m paper_feed import-legacy [--root R] [--database D] [--dry-run]", file=sys.stderr)
    args = parser.parse_args(argv)
    if not getattr(args, "handler", None):
        parser.print_help()
        return EXIT_OK
    try:
        return args.handler(args)
    except KeyboardInterrupt:
        print("\nInterrupted. 已中断。")
        return 130


if __name__ == "__main__":
    sys.exit(main())
