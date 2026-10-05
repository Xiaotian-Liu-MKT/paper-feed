import feedparser
import re
import os
import sys
import datetime
import time
import json
import hashlib
import tempfile
import functools
import html
import threading
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
from rfeed import Item, Feed, Guid
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, unquote
from paper_feed.ingestion import ingest_fetch_results, ensure_database, save_translations as save_db_translations, save_abstracts as save_db_abstracts
from paper_feed.exporter import database_items, export_items
from paper_feed.locks import LockBusyError, job_lock  # noqa: F401 (LockBusyError re-exported)
from paper_feed import taste as taste_store

# --- 配置区域 ---
# All project files resolve relative to this file, never the current directory,
# so `python E:\...\get_RSS.py` behaves the same from any working directory.
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(BASE_DIR, "filtered_feed.xml")
WEB_DIR = os.path.join(BASE_DIR, "web")
FEED_JSON = os.path.join(WEB_DIR, "feed.json")
JOURNAL_HASH_FILE = os.path.join(WEB_DIR, "journals.hash")
TRANSLATIONS_CACHE = os.path.join(WEB_DIR, "translations.json")
ABSTRACTS_CACHE = os.path.join(WEB_DIR, "abstracts.json")
CATEGORIES_FILE = os.path.join(WEB_DIR, "categories.json")
USER_CORRECTIONS_FILE = os.path.join(WEB_DIR, "user_corrections.json")
MAX_ITEMS = 1000
RSS_FETCH_WORKERS = 8
RSS_REQUEST_TIMEOUT = (5, 20)
AI_ANALYSIS_WORKERS = 5
ABSTRACT_FETCH_WORKERS = 5
CLASSIFICATION_VERSION = "v2"

JOURNALS_FILE = os.path.join(BASE_DIR, "journals.dat")
KEYWORDS_FILE = os.path.join(BASE_DIR, "keywords.dat")

# OpenAI 配置
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
CONFIG_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_PROXY", "OPENAI_MODEL",
               "AI_BACKEND", "CODEX_MODEL", "CODEX_REASONING_EFFORT", "CODEX_PATH")
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
# AI backends: "codex" runs the locally installed Codex CLI (`codex exec`, ChatGPT
# login, subscription quota); "openai" calls an OpenAI-compatible HTTP API.
AI_BACKENDS = ("codex", "openai")
DEFAULT_AI_BACKEND = "codex"
DEFAULT_CODEX_MODEL = "gpt-6-luna"
DEFAULT_CODEX_REASONING_EFFORT = "low"
CONFIG_DEFAULTS = {"OPENAI_MODEL": DEFAULT_OPENAI_MODEL, "AI_BACKEND": DEFAULT_AI_BACKEND,
                   "CODEX_MODEL": DEFAULT_CODEX_MODEL, "CODEX_REASONING_EFFORT": DEFAULT_CODEX_REASONING_EFFORT}
OPENAI_RETRY_ATTEMPTS = 3
OPENAI_RETRY_BASE_DELAY = 2.0
# Per-request ceiling.  Timeouts are not retried and a shared CircuitBreaker
# stops a job after repeated timeouts, so a hung endpoint costs ~1-2 timeouts.
OPENAI_TIMEOUT_SECONDS = 60
OPENAI_BREAKER_THRESHOLD = 2
# Codex CLI: every `codex exec` carries ~20k tokens of system-prompt overhead
# (ChatGPT subscription quota), so it gets fewer, larger calls.
CODEX_TIMEOUT_SECONDS = 180
CODEX_ANALYSIS_CHUNK_SIZE = 25
CODEX_ANALYSIS_WORKERS = 2
OPENAI_ANALYSIS_CHUNK_SIZE = 10
# On-demand summaries: Codex batches several papers per `codex exec` (one
# ~20k-token overhead per call); the OpenAI API keeps one paper per call.
CODEX_SUMMARY_BATCH_SIZE = 8
OPENAI_SUMMARY_BATCH_SIZE = 1
CODEX_SUMMARY_WORKERS = 2
# Extra Codex timeout per paper in a summary batch (on top of CODEX_TIMEOUT_SECONDS).
CODEX_SUMMARY_SECONDS_PER_ITEM = 20
TITLE_ONLY_PREFIX = "【基于标题推测，未读原文】"
# Free abstract sources (no tokens): short timeouts, never raise to callers.
ABSTRACT_HTTP_TIMEOUT = (5, 10)
ABSTRACT_MIN_LENGTH = 100
ABSTRACT_SOURCE_BREAKER_THRESHOLD = 3
FETCHED_ABSTRACT_SOURCES = ("crossref", "openalex", "semantic_scholar")
ABSTRACT_USER_AGENT = "Paper-Feed/1.0 (+https://github.com/Xiaotian-Liu-MKT/paper-feed)"
MAX_REPORTED_ERRORS = 20
UNCLASSIFIED_LABEL = "Unclassified"
DEFAULT_CLASSIFICATION_DOMAIN = "Business & Marketing"


def configure_stdio():
    """Avoid UnicodeEncodeError on legacy Windows consoles (GBK/cp1252)."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def atomic_write(path, content, encoding="utf-8"):
    """Durably replace *path* without exposing a partially-written file."""
    directory = os.path.dirname(os.path.abspath(path))
    os.makedirs(directory, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=".tmp-", dir=directory)
    try:
        if isinstance(content, bytes):
            handle = os.fdopen(fd, "wb")
        else:
            handle = os.fdopen(fd, "w", encoding=encoding)
        with handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except FileNotFoundError:
            pass
        raise

def is_usable_config_value(value):
    """Empty values and template placeholders (e.g. "your-api-key-here") are unset."""
    if value is None:
        return False
    if not isinstance(value, str):
        return True
    text = value.strip()
    return bool(text) and not text.lower().startswith("your-")


def get_config():
    """Resolve settings: non-empty env var > usable config.json value > default."""
    local_config = {}
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                local_config = loaded
        except Exception as e:
            print(f"Error reading config file: {e}")

    # Unknown keys are preserved for forward compatibility.
    config = {key: value for key, value in local_config.items() if key not in CONFIG_KEYS}
    for key in CONFIG_KEYS:
        env_value = os.environ.get(key)
        file_value = local_config.get(key)
        if is_usable_config_value(env_value):
            config[key] = env_value.strip()
        elif is_usable_config_value(file_value):
            config[key] = file_value.strip() if isinstance(file_value, str) else file_value
        else:
            config[key] = CONFIG_DEFAULTS.get(key)
    return config


def config_model(config):
    return (config or {}).get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL


# --- AI backend selection (Codex CLI by default, OpenAI-compatible API optional) ---

# Values passed on the command line of `codex exec` must be plain tokens: the
# npm shim is a Windows .cmd file, so quotes or shell metacharacters would be
# re-interpreted by cmd.exe.
_CODEX_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_CODEX_FALLBACK_LOGGED = False


def is_safe_codex_token(value):
    return isinstance(value, str) and bool(_CODEX_TOKEN_RE.match(value)) and len(value) <= 100


def configured_backend(config):
    """The configured AI_BACKEND ("codex" or "openai"); unknown values mean the default."""
    value = str((config or {}).get("AI_BACKEND") or "").strip().lower()
    return value if value in AI_BACKENDS else DEFAULT_AI_BACKEND


def find_codex_executable(config=None):
    """Path of the Codex CLI executable, or None.

    An explicit CODEX_PATH wins (a bare npm shim path without extension is
    mapped to its ``.cmd`` sibling on Windows); otherwise ``codex`` is looked
    up on PATH.  This is the single discovery point (tests patch it).
    """
    import shutil
    explicit = (config or {}).get("CODEX_PATH")
    if isinstance(explicit, str) and explicit.strip():
        path = os.path.expandvars(os.path.expanduser(explicit.strip().strip('"')))
        if os.name == "nt" and not os.path.splitext(path)[1]:
            for suffix in (".cmd", ".exe", ".bat"):
                if os.path.isfile(path + suffix):
                    return path + suffix
        if os.path.isfile(path):
            return path
        return shutil.which(path)
    return shutil.which("codex")


class CodexCLISettings:
    """Settings of the Codex CLI backend.

    Passed in place of ``api_key`` to the AI helpers (``batch_analyze_papers``,
    ``generate_abstract_with_gpt``, ``summarize_abstract_with_gpt``,
    ``make_openai_client``): it is truthy, so the existing "no key -> skip"
    guards keep working, and ``make_openai_client`` turns it into a
    ``CodexCLIClient``.  It holds no secret.
    """

    def __init__(self, executable, model=None, reasoning_effort=None, timeout=CODEX_TIMEOUT_SECONDS):
        self.executable = executable
        self.model = model or DEFAULT_CODEX_MODEL
        self.reasoning_effort = reasoning_effort or DEFAULT_CODEX_REASONING_EFFORT
        self.timeout = timeout

    def __bool__(self):
        return True

    def __repr__(self):
        return f"CodexCLISettings(model={self.model!r}, reasoning_effort={self.reasoning_effort!r})"

    __str__ = __repr__


def is_codex_backend(api_key):
    return isinstance(api_key, CodexCLISettings)


def ai_settings(config=None, log=False):
    """Resolve the effective AI backend.

    Returns ``{backend, configured_backend, api_key, base_url, proxy, model,
    ready, reason, codex_available, codex_path}``.  ``backend`` is "codex",
    "openai" or None (not ready: AI steps are skipped).  For codex,
    ``api_key`` is a ``CodexCLISettings`` (see there) and ``model`` the Codex
    model.  AI_BACKEND=codex without an installed Codex CLI (e.g. GitHub
    Actions) falls back to the OpenAI API when OPENAI_API_KEY is set.
    """
    global _CODEX_FALLBACK_LOGGED
    config = get_config() if config is None else config
    configured = configured_backend(config)
    openai_key = config.get("OPENAI_API_KEY")
    settings = {"backend": None, "configured_backend": configured, "api_key": None,
                "base_url": config.get("OPENAI_BASE_URL"), "proxy": config.get("OPENAI_PROXY"),
                "model": None, "ready": False, "reason": "", "codex_available": False, "codex_path": None}
    codex_path = find_codex_executable(config) if configured == "codex" else None
    settings["codex_available"] = bool(codex_path)
    settings["codex_path"] = codex_path
    if configured == "codex" and codex_path:
        model = config.get("CODEX_MODEL") or DEFAULT_CODEX_MODEL
        effort = config.get("CODEX_REASONING_EFFORT") or DEFAULT_CODEX_REASONING_EFFORT
        if not (is_safe_codex_token(model) and is_safe_codex_token(effort)):
            settings["reason"] = ("CODEX_MODEL / CODEX_REASONING_EFFORT contain unsupported characters "
                                  "(letters, digits and . _ : / - only)")
            return settings
        settings.update(backend="codex", model=model, ready=True,
                        api_key=CodexCLISettings(codex_path, model, effort))
        return settings
    if openai_key:
        if configured == "codex" and log and not _CODEX_FALLBACK_LOGGED:
            _CODEX_FALLBACK_LOGGED = True
            print("Codex CLI not found; falling back to the OpenAI API (OPENAI_API_KEY). "
                  "未找到 Codex CLI，改用 OpenAI API。")
        settings.update(backend="openai", api_key=openai_key, model=config_model(config), ready=True)
        return settings
    if configured == "codex":
        settings["reason"] = ("Codex CLI not found and no OPENAI_API_KEY configured "
                              "(install it with `npm i -g @openai/codex` and run `codex login`, or set CODEX_PATH)")
    else:
        settings["reason"] = "no OPENAI_API_KEY configured (AI_BACKEND=openai)"
    return settings


def ai_backend_label(settings):
    if (settings or {}).get("backend") == "codex":
        return f"Codex CLI ({settings.get('model')})"
    if (settings or {}).get("backend") == "openai":
        return f"OpenAI API ({settings.get('model')})"
    return "no AI backend"


def ai_skip_message(settings):
    return f"no AI backend available: {(settings or {}).get('reason') or 'not configured'}"


class CodexCLIError(RuntimeError):
    """Base class of Codex CLI failures (messages carry the stderr tail, never secrets)."""


class CodexCLINotFoundError(CodexCLIError):
    """The executable is missing: fail fast, never retried."""


class CodexCLITimeoutError(CodexCLIError):
    """`codex exec` exceeded its timeout: counts toward the circuit breaker."""


class CodexCLIProcessError(CodexCLIError):
    """Non-zero exit or empty output: counts toward the circuit breaker."""


def _message_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(part for part in parts if part)
    return "" if content is None else str(content)


def flatten_messages(messages, json_output=False):
    """One prompt for `codex exec`: system messages first, then the conversation."""
    messages = [message for message in (messages or []) if isinstance(message, dict)]
    system = [_message_text(m.get("content")) for m in messages if m.get("role") == "system"]
    rest = []
    for message in messages:
        role = message.get("role")
        if role == "system":
            continue
        text = _message_text(message.get("content"))
        rest.append(text if role in (None, "user") else f"[{role}]\n{text}")
    blocks = [text.strip() for text in system + rest if text and text.strip()]
    if json_output:
        blocks.append("Output only valid JSON, no code fences and no other text.")
    return "\n\n".join(blocks)


_FENCE_RE = re.compile(r"^\s*```[A-Za-z0-9_-]*[ \t]*\n(.*?)\n?\s*```\s*$", re.DOTALL)


def strip_code_fences(text):
    text = (text or "").strip()
    match = _FENCE_RE.match(text)
    return match.group(1).strip() if match else text


def parse_model_json(text):
    """Parse a model's JSON reply, tolerating fences, trailing prose and several concatenated values.

    Codex sometimes emits ``{"results": [...]}`` twice, or one item object per
    line; ``results`` lists are merged and bare item objects are collected
    into ``{"results": [...]}``.  Raises ValueError when no JSON is found.
    """
    text = strip_code_fences(text)
    try:
        return json.loads(text)
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    values, index = [], 0
    while True:
        starts = [pos for pos in (text.find("{", index), text.find("[", index)) if pos != -1]
        if not starts:
            break
        try:
            value, index = decoder.raw_decode(text, min(starts))
        except ValueError:
            index = min(starts) + 1
            continue
        values.append(value)
    if not values:
        raise ValueError("model reply contained no JSON")
    if len(values) == 1:
        return values[0]
    results = []
    for value in values:
        if isinstance(value, dict) and isinstance(value.get("results"), list):
            results.extend(value["results"])
        elif isinstance(value, list):
            results.extend(value)
        elif isinstance(value, dict):
            results.append(value)
    return {"results": results}


def _stderr_tail(data, limit=300):
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    text = " ".join(str(data or "").split())
    for name in ("OPENAI_API_KEY", "CODEX_API_KEY"):
        secret = os.environ.get(name)
        if secret and len(secret) > 4:
            text = text.replace(secret, "***")
    return text[-limit:]


def build_codex_command(settings, output_path):
    return [settings.executable, "exec", "-m", settings.model,
            "-c", f"model_reasoning_effort={settings.reasoning_effort}",
            "--ephemeral", "--skip-git-repo-check", "-s", "read-only", "--ignore-rules",
            "-o", output_path, "-"]


def run_codex_exec(settings, prompt, timeout=None):
    """Run `codex exec` once with *prompt* on stdin; return the final message text."""
    import subprocess
    import shutil
    timeout = timeout or settings.timeout or CODEX_TIMEOUT_SECONDS
    if not settings.executable:
        raise CodexCLINotFoundError("Codex CLI executable not found (npm i -g @openai/codex, or set CODEX_PATH)")
    workdir = tempfile.mkdtemp(prefix="paper-feed-codex-")
    try:
        output_path = os.path.join(workdir, "last_message.txt")
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            completed = subprocess.run(build_codex_command(settings, output_path),
                                       input=(prompt or "").encode("utf-8"), capture_output=True,
                                       cwd=workdir, timeout=timeout, **kwargs)
        except FileNotFoundError as error:
            raise CodexCLINotFoundError(f"Codex CLI executable not found: {settings.executable}") from error
        except subprocess.TimeoutExpired as error:
            raise CodexCLITimeoutError(f"codex exec timed out after {timeout:.0f}s; "
                                       f"stderr: {_stderr_tail(error.stderr)}") from None
        if completed.returncode != 0:
            raise CodexCLIProcessError(f"codex exec exited with code {completed.returncode}; "
                                       f"stderr: {_stderr_tail(completed.stderr)}")
        try:
            with open(output_path, "r", encoding="utf-8") as handle:
                content = handle.read()
        except OSError:
            content = ""
        content = strip_code_fences(content)
        if not content:
            raise CodexCLIProcessError(f"codex exec produced no output; stderr: {_stderr_tail(completed.stderr)}")
        return content
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


class _Namespace:
    def __init__(self, **values):
        self.__dict__.update(values)


class CodexCLIClient:
    """Minimal stand-in for the OpenAI client backed by `codex exec`.

    Supports ``client.chat.completions.create(model=..., messages=[...], ...)``
    (the OpenAI model name, max_tokens and temperature are ignored: Codex uses
    CODEX_MODEL) and ``client.with_options(timeout=...)``.  The response has
    ``.choices[0].message.content`` and ``.usage = None``.
    """

    def __init__(self, settings, timeout=None):
        self.settings = settings
        self.timeout = timeout or settings.timeout or CODEX_TIMEOUT_SECONDS
        self.chat = _Namespace(completions=_Namespace(create=self._create))

    def with_options(self, max_retries=None, timeout=None, **ignored):
        return CodexCLIClient(self.settings, timeout if timeout is not None else self.timeout)

    def _create(self, model=None, messages=None, max_tokens=None, temperature=None,
                response_format=None, **ignored):
        json_output = isinstance(response_format, dict) and response_format.get("type") in ("json_object", "json_schema")
        content = run_codex_exec(self.settings, flatten_messages(messages, json_output), self.timeout)
        message = _Namespace(role="assistant", content=content)
        return _Namespace(choices=[_Namespace(index=0, message=message, finish_reason="stop")],
                          usage=None, model=self.settings.model)


def add_error(report, message):
    """Record a failure in a job report; the error list is capped for the UI."""
    if report is None:
        return
    errors = report.setdefault("errors", [])
    if len(errors) < MAX_REPORTED_ERRORS:
        errors.append(str(message)[:300])


def make_openai_client(api_key, base_url=None, proxy=None):
    """Create an OpenAI client.  httpx>=0.28 accepts only `proxy=`, not `proxies=`.

    The SDK's own retries are disabled (``max_retries=0``) so that
    ``chat_completion_with_retry`` is the single retry mechanism, and every
    request is bounded by ``OPENAI_TIMEOUT_SECONDS``.  When *api_key* is a
    ``CodexCLISettings`` (see ``ai_settings``) a ``CodexCLIClient`` is returned.
    """
    if is_codex_backend(api_key):
        return CodexCLIClient(api_key)
    from openai import OpenAI
    import httpx

    http_client = httpx.Client(proxy=proxy) if proxy else None
    return OpenAI(api_key=api_key, base_url=base_url or None, http_client=http_client,
                  timeout=OPENAI_TIMEOUT_SECONDS, max_retries=0)


class CircuitBreaker:
    """Stop calling an endpoint that keeps timing out / refusing connections.

    One breaker is shared by all calls of a job (thread-safe).  After
    *threshold* consecutive network failures it opens and every later call
    fails fast, so a hung endpoint costs roughly one timeout per worker
    instead of one timeout per paper.
    """

    def __init__(self, threshold=2, name="service"):
        self.threshold = max(1, threshold)
        self.name = name
        self._failures = 0
        self._lock = threading.Lock()

    @property
    def is_open(self):
        with self._lock:
            return self._failures >= self.threshold

    def record_success(self):
        with self._lock:
            self._failures = 0

    def record_failure(self):
        with self._lock:
            self._failures += 1


class CircuitOpenError(RuntimeError):
    """Raised instead of calling an endpoint whose circuit breaker is open."""


def is_timeout_like_openai_error(error):
    """Timeouts and connection failures: the endpoint is hung or unreachable."""
    if isinstance(error, CodexCLIError):
        # Missing executable, timeouts and failing runs all mean "stop calling".
        return True
    try:
        import openai
    except ImportError:
        return False
    connection = getattr(openai, "APIConnectionError", None)
    return bool(connection and isinstance(error, connection))


def is_retryable_openai_error(error):
    if isinstance(error, CodexCLIError):
        # codex retries its own transport; retrying a timeout, a missing
        # executable or a failed run would only burn quota again.
        return False
    try:
        import openai
    except ImportError:
        return False
    # A timed-out request already waited OPENAI_TIMEOUT_SECONDS; retrying it
    # would multiply the time a hung endpoint can block a job.
    timeout_error = getattr(openai, "APITimeoutError", None)
    if timeout_error and isinstance(error, timeout_error):
        return False
    retryable = tuple(cls for cls in (getattr(openai, "RateLimitError", None),
                                      getattr(openai, "APIConnectionError", None)) if cls)
    if retryable and isinstance(error, retryable):
        return True
    status_error = getattr(openai, "APIStatusError", None)
    if status_error and isinstance(error, status_error):
        return (getattr(error, "status_code", 0) or 0) >= 500
    return False


def chat_completion_with_retry(client, attempts=None, breaker=None, **kwargs):
    """Call chat.completions.create with exponential backoff on transient errors.

    This is the only retry layer (the SDK client is built with max_retries=0).
    When a shared *breaker* is open the call fails fast with CircuitOpenError.
    """
    attempts = max(1, attempts or OPENAI_RETRY_ATTEMPTS)
    for attempt in range(1, attempts + 1):
        if breaker is not None and breaker.is_open:
            raise CircuitOpenError(f"{breaker.name} is not responding; skipped remaining requests")
        try:
            response = client.chat.completions.create(**kwargs)
        except Exception as error:
            if breaker is not None and is_timeout_like_openai_error(error):
                breaker.record_failure()
            if attempt >= attempts or not is_retryable_openai_error(error):
                raise
            delay = OPENAI_RETRY_BASE_DELAY * (2 ** (attempt - 1))
            print(f"OpenAI transient error ({type(error).__name__}); retrying in {delay:.0f}s "
                  f"(attempt {attempt}/{attempts})...")
            time.sleep(delay)
        else:
            if breaker is not None:
                breaker.record_success()
            return response


def fallback_label(configured_names, preferred):
    """Legacy default when configured (or nothing configured), otherwise Unclassified."""
    names = [name for name in (configured_names or []) if name]
    if not names or preferred in names:
        return preferred
    return UNCLASSIFIED_LABEL

# ----------------

def extract_doi(link, entry_id=''):
    """从链接或 entry ID 中提取 DOI"""
    # 常见 DOI 格式: 10.xxxx/xxxxx
    doi_pattern = r'10\.\d{4,}/[^\s<>"\'\)\]]+(?=[<>"\'\)\]\s]|$)'

    # 尝试从链接中提取
    for text in [link, entry_id]:
        if not text:
            continue
        # 解码 URL
        decoded = unquote(text)
        match = re.search(doi_pattern, decoded)
        if match:
            doi = match.group(0)
            # 清理末尾的标点
            doi = doi.rstrip('.,;:!?')
            return doi

    # 特定网站的 DOI 提取
    if 'sciencedirect.com' in link:
        # ScienceDirect: pii 可以转换为 DOI
        pii_match = re.search(r'/pii/([A-Z0-9]+)', link)
        if pii_match:
            return f"pii:{pii_match.group(1)}"

    return None

def clean_abstract_text(text):
    """Plain text from a Crossref/JATS/HTML abstract (tags, entities, 'Abstract' heading removed)."""
    if not text:
        return ""
    text = str(text)
    # A JATS/HTML heading that only says "Abstract" is not part of the abstract.
    text = re.sub(r'<(?:jats:)?title[^>]*>\s*(?:abstract|summary)\s*[.:]?\s*</(?:jats:)?title>', ' ', text, flags=re.IGNORECASE)
    text = re.sub(r'<(?:jats:)?(?:p|sec|title|list-item)\b[^>]*>', ' ', text, flags=re.IGNORECASE)
    text = strip_tags(text)
    text = html.unescape(text)
    text = re.sub(r'\s+', ' ', text).strip()
    text = re.sub(r'^(?:abstract|summary)\s*[.:]?\s+', '', text, flags=re.IGNORECASE)
    return text.strip()


def openalex_mailto(config=None):
    """Optional polite-pool contact for OpenAlex (OPENALEX_MAILTO env var or config.json)."""
    value = os.environ.get("OPENALEX_MAILTO")
    if not is_usable_config_value(value):
        if config is None:
            try:
                config = get_config()
            except Exception:
                config = {}
        value = (config or {}).get("OPENALEX_MAILTO")
    return value.strip() if is_usable_config_value(value) and isinstance(value, str) else None


# Crossref's public pool allows one request at a time and about one per second
# (x-concurrency-limit / x-rate-limit headers), so Crossref calls are serialized.
_CROSSREF_LOCK = threading.Lock()
_CROSSREF_MIN_INTERVAL = 1.0
_crossref_last_call = [0.0]
ABSTRACT_429_RETRIES = 2


def _retry_after_seconds(response, attempt):
    try:
        value = float((getattr(response, "headers", None) or {}).get("Retry-After"))
    except (TypeError, ValueError):
        value = 2.0 * (attempt + 1)
    return max(0.5, min(value, 10.0))


def _abstract_http_get(url, source, params=None):
    if source != "Crossref":
        return requests.get(url, params=params, timeout=ABSTRACT_HTTP_TIMEOUT,
                            headers={"User-Agent": ABSTRACT_USER_AGENT, "Accept": "application/json"})
    with _CROSSREF_LOCK:
        wait = _CROSSREF_MIN_INTERVAL - (time.monotonic() - _crossref_last_call[0])
        if wait > 0:
            time.sleep(wait)
        try:
            return requests.get(url, params=params, timeout=ABSTRACT_HTTP_TIMEOUT,
                                headers={"User-Agent": ABSTRACT_USER_AGENT, "Accept": "application/json"})
        finally:
            _crossref_last_call[0] = time.monotonic()


def _abstract_api_get(url, source, params=None, breaker=None):
    """GET JSON from a free metadata API; returns dict or None and never raises.

    A 429 is retried (honouring Retry-After, capped) before it counts as a failure.
    """
    if breaker is not None and breaker.is_open:
        return None
    for attempt in range(ABSTRACT_429_RETRIES + 1):
        try:
            response = _abstract_http_get(url, source, params)
        except Exception as e:
            print(f"{source} request failed: {type(e).__name__}: {e}")
            if breaker is not None:
                breaker.record_failure()
            return None
        if getattr(response, "status_code", None) != 429 or attempt == ABSTRACT_429_RETRIES:
            break
        time.sleep(_retry_after_seconds(response, attempt))
    status = getattr(response, "status_code", None)
    if status == 429 or (isinstance(status, int) and status >= 500):
        if breaker is not None:
            breaker.record_failure()
        return None
    if breaker is not None:
        breaker.record_success()
    if status != 200:
        return None
    try:
        data = response.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _usable_doi(doi):
    return bool(doi) and not str(doi).startswith('pii:')


def is_elsevier_doi(doi):
    """Elsevier (10.1016) does not deposit abstracts with Crossref, so that lookup is skipped."""
    return str(doi or "").lower().startswith("10.1016/")


def get_abstract_from_crossref(doi, breaker=None):
    """从 Crossref API 获取摘要 (JATS tags stripped).  Never raises."""
    if not _usable_doi(doi):
        return None
    try:
        data = _abstract_api_get(f"https://api.crossref.org/works/{doi}", "Crossref", breaker=breaker)
        message = (data or {}).get('message') or {}
        return clean_abstract_text(message.get('abstract')) if isinstance(message, dict) else None
    except Exception as e:
        print(f"Crossref API error for DOI {doi}: {e}")
        return None


def reconstruct_inverted_index(inverted_index):
    """Rebuild OpenAlex `abstract_inverted_index` ({word: [positions]}) into text."""
    if not isinstance(inverted_index, dict):
        return ""
    positions = []
    for word, indexes in inverted_index.items():
        if not isinstance(indexes, list):
            continue
        for index in indexes:
            if isinstance(index, int) and index >= 0:
                positions.append((index, str(word)))
    positions.sort()
    return " ".join(word for _, word in positions)


def get_abstract_from_openalex(doi, mailto=None, breaker=None):
    """从 OpenAlex 获取摘要（由 abstract_inverted_index 还原）.  Never raises."""
    if not _usable_doi(doi):
        return None
    try:
        params = {"mailto": mailto} if mailto else None
        data = _abstract_api_get(f"https://api.openalex.org/works/https://doi.org/{doi}", "OpenAlex",
                                 params=params, breaker=breaker)
        return clean_abstract_text(reconstruct_inverted_index((data or {}).get("abstract_inverted_index"))) or None
    except Exception as e:
        print(f"OpenAlex API error for DOI {doi}: {e}")
        return None


def get_abstract_from_semantic_scholar(title, doi=None, breaker=None):
    """从 Semantic Scholar API 获取摘要.  With a DOI the exact record is used (abstract only);
    without one the legacy title search is used (TL;DR + abstract).  Never raises."""
    if _usable_doi(doi):
        try:
            data = _abstract_api_get(f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}", "Semantic Scholar",
                                     params={"fields": "abstract"}, breaker=breaker)
            return clean_abstract_text((data or {}).get("abstract")) or None
        except Exception as e:
            print(f"Semantic Scholar API error for DOI {doi}: {e}")
            return None
    if not title:
        return None

    try:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            'query': title,
            'limit': 1,
            'fields': 'abstract,tldr,citationCount,influentialCitationCount'
        }
        data = _abstract_api_get(url, "Semantic Scholar", params=params, breaker=breaker)
        if data and data.get('data') and len(data['data']) > 0:
            paper = data['data'][0]
            abstract = clean_abstract_text(paper.get('abstract') or '')
            # 优先使用 TL;DR（更简洁）
            tldr = paper.get('tldr') or {}
            if tldr and tldr.get('text'):
                return f"{tldr['text']}\n\n{abstract}" if abstract else tldr['text']
            return abstract
    except Exception as e:
        print(f"Semantic Scholar API error for title '{title[:50]}...': {e}")

    return None

def generate_abstract_with_gpt(title, journal, api_key, base_url=None, proxy=None, model=None, errors=None, breaker=None):
    """使用 GPT 基于标题推测研究方向（仅标题，未读原文，属于推测）"""
    if not title or not api_key:
        return None

    try:
        client = make_openai_client(api_key, base_url, proxy)

        prompt = f"""Only the title and journal of an academic paper are available below. You have NOT read the paper or its abstract.
Write a short (about 120 words) Chinese note that GUESSES what the study may investigate, based on the title only.

Rules:
- This is a speculative guess, not a summary. Use hedged wording such as "可能"、"推测"、"或许" throughout.
- Do not invent specific findings, sample sizes, effect directions, statistics, or study counts.
- Start the note with "【基于标题推测，未读原文】".
- No HTML tags or angle brackets.

Title: {title}
Journal: {journal}

Cover briefly: 可能的研究主题、可能的研究方法、可能的贡献。"""

        response = chat_completion_with_retry(
            client,
            breaker=breaker,
            model=model or DEFAULT_OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a careful academic research assistant. When information is missing you say so and only speculate with explicit hedging, in Chinese."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
            temperature=0.2
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"GPT abstract generation error: {e}")
        if errors is not None:
            errors.append(f"{type(e).__name__}: {e}")
        return None

def summarize_abstract_with_gpt(abstract, title, api_key, base_url=None, proxy=None, model=None, errors=None, breaker=None):
    """使用 GPT 基于已有摘要生成中文学术总结"""
    if not abstract or not api_key:
        return None

    try:
        client = make_openai_client(api_key, base_url, proxy)

        prompt = f"""Summarize the following academic abstract in Chinese (120-150 words). Keep it academic, concise, and objective. Avoid any HTML tags or angle brackets.

Title: {title}
Abstract: {abstract}

Provide a structured summary covering: 研究主题、可能的研究方法、主要贡献。"""

        response = chat_completion_with_retry(
            client,
            breaker=breaker,
            model=model or DEFAULT_OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are an academic research assistant. Generate concise, academic-style research summaries in Chinese."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
            temperature=0.4
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"GPT abstract summary error: {e}")
        if errors is not None:
            errors.append(f"{type(e).__name__}: {e}")
        return None

SUMMARY_BATCH_MAX_ABSTRACT_CHARS = 6000
_ANGLE_TAG_RE = re.compile(r"<[^>]*>")


def _clean_summary_text(text, title_only):
    text = _ANGLE_TAG_RE.sub("", str(text or "")).replace("<", "").replace(">", "").strip()
    if text and title_only and not text.startswith(TITLE_ONLY_PREFIX):
        # The provenance marker is mandatory for a title-only guess.
        text = TITLE_ONLY_PREFIX + text
    return text


def summary_batch_prompt(entries):
    """One prompt for several papers; the per-mode instructions mirror
    ``summarize_abstract_with_gpt`` (MODE: ABSTRACT) and
    ``generate_abstract_with_gpt`` (MODE: TITLE-ONLY)."""
    blocks = []
    for position, entry in enumerate(entries, 1):
        raw = str(entry.get("raw") or "")
        if raw:
            if len(raw) > SUMMARY_BATCH_MAX_ABSTRACT_CHARS:
                raw = raw[:SUMMARY_BATCH_MAX_ABSTRACT_CHARS] + " ..."
            blocks.append(f"[{position}] MODE: ABSTRACT\nTitle: {entry.get('title') or ''}\n"
                          f"Journal: {entry.get('journal') or ''}\nAbstract: {raw}")
        else:
            blocks.append(f"[{position}] MODE: TITLE-ONLY\nTitle: {entry.get('title') or ''}\n"
                          f"Journal: {entry.get('journal') or ''}")
    return f"""Write one short Chinese note for each of the {len(entries)} academic papers below. Each paper is marked MODE: ABSTRACT or MODE: TITLE-ONLY.

MODE: ABSTRACT - summarize the given abstract in Chinese (120-150 words). Keep it academic, concise, and objective. Provide a structured summary covering: 研究主题、可能的研究方法、主要贡献。

MODE: TITLE-ONLY - only the title and journal are available. You have NOT read the paper or its abstract. Write a short (about 120 words) Chinese note that GUESSES what the study may investigate, based on the title only.
- This is a speculative guess, not a summary. Use hedged wording such as "可能"、"推测"、"或许" throughout.
- Do not invent specific findings, sample sizes, effect directions, statistics, or study counts.
- Start the note with "{TITLE_ONLY_PREFIX}".
- Cover briefly: 可能的研究主题、可能的研究方法、可能的贡献。

Rules for every paper:
- Treat each paper independently; never mix information between papers.
- No HTML tags or angle brackets.
- Output valid JSON: {{"results": [{{"index": 1, "summary": "..."}}]}} with exactly one result per paper; "index" is the paper's number in brackets, copied exactly (1-based).

Papers:

""" + "\n\n".join(blocks)


def summarize_batch_with_gpt(entries, api_key, base_url=None, proxy=None, model=None, breaker=None):
    """Summarize several papers in one AI call.

    *entries*: ``[{"title", "journal", "raw"}]`` (``raw`` = raw abstract or
    empty for a title-only guess).  Returns ``[(summary | None, error | None)]``
    in input order.  Results are aligned by the echoed 1-based ``index``
    (``align_batch_results``); unaligned items get an error and stay pending.
    Never raises.
    """
    if not entries:
        return []
    if not api_key:
        return [(None, "no AI backend") for _ in entries]
    try:
        client = make_openai_client(api_key, base_url, proxy)
        if is_codex_backend(api_key):
            # A larger batch needs a longer `codex exec`.
            base = max(api_key.timeout or CODEX_TIMEOUT_SECONDS, CODEX_TIMEOUT_SECONDS)
            client = client.with_options(timeout=base + CODEX_SUMMARY_SECONDS_PER_ITEM * len(entries))
        response = chat_completion_with_retry(
            client,
            breaker=breaker,
            model=model or DEFAULT_OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a careful academic research assistant and a JSON-only API. "
                                              "You write concise academic-style notes in Chinese; when information is "
                                              "missing you say so and only speculate with explicit hedging."},
                {"role": "user", "content": summary_batch_prompt(entries)},
            ],
            max_tokens=450 * len(entries),
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        data = parse_model_json(response.choices[0].message.content or "")
    except Exception as e:
        print(f"GPT batch summary error: {e}")
        reason = f"{type(e).__name__}: {e}"
        return [(None, reason) for _ in entries]
    result_list = data.get("results", []) if isinstance(data, dict) else []
    if not isinstance(result_list, list):
        result_list = []
    outcomes = [(None, "not returned by the model; left pending") for _ in entries]
    for position, result in align_batch_results(list(range(len(entries))), result_list):
        summary = result.get("summary") if isinstance(result, dict) else None
        summary = _clean_summary_text(summary, not entries[position].get("raw")) if isinstance(summary, str) else ""
        outcomes[position] = (summary, None) if summary else (None, "empty summary in batch response")
    missing = sum(1 for summary, _ in outcomes if not summary)
    if missing:
        print(f"Warning: batch summary returned {len(result_list)} items for {len(entries)} papers; "
              f"{missing} left pending.")
    return outcomes


def _summarize_one(entry, api_key, base_url, proxy, model, breaker):
    errors = []
    if entry.get("raw"):
        summary = summarize_abstract_with_gpt(entry["raw"], entry.get("title"), api_key, base_url, proxy,
                                              model=model, errors=errors, breaker=breaker)
    else:
        summary = generate_abstract_with_gpt(entry.get("title"), entry.get("journal"), api_key, base_url, proxy,
                                             model=model, errors=errors, breaker=breaker)
    return (summary, None) if summary else (None, errors[0] if errors else "empty response")


def generate_summaries(entries, api_key, base_url=None, proxy=None, model=None, breaker=None):
    """``[(summary | None, error | None)]`` for *entries* (see ``summarize_batch_with_gpt``).

    Codex: ``CODEX_SUMMARY_BATCH_SIZE`` papers per call; OpenAI:
    ``OPENAI_SUMMARY_BATCH_SIZE`` (1 = the per-paper prompts, sequentially).
    A shared *breaker* stops the remaining calls after repeated timeouts.
    """
    codex = is_codex_backend(api_key)
    size = max(1, CODEX_SUMMARY_BATCH_SIZE if codex else OPENAI_SUMMARY_BATCH_SIZE)
    if size == 1:
        return [_summarize_one(entry, api_key, base_url, proxy, model, breaker) for entry in entries]
    chunks = [list(range(start, min(start + size, len(entries)))) for start in range(0, len(entries), size)]
    outcomes = [(None, "not processed") for _ in entries]

    def run(positions):
        if len(positions) == 1:
            return positions, [_summarize_one(entries[positions[0]], api_key, base_url, proxy, model, breaker)]
        return positions, summarize_batch_with_gpt([entries[p] for p in positions], api_key, base_url, proxy,
                                                   model=model, breaker=breaker)

    workers = min(CODEX_SUMMARY_WORKERS if codex else AI_ANALYSIS_WORKERS, len(chunks)) or 1
    print(f"Summarizing {len(entries)} papers in {len(chunks)} batch(es) of up to {size}...")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for positions, results in executor.map(run, chunks):
            for position, outcome in zip(positions, results):
                outcomes[position] = outcome
    return outcomes


def abstract_breakers():
    """One breaker per free source, shared by every lookup of a job."""
    return {source: CircuitBreaker(ABSTRACT_SOURCE_BREAKER_THRESHOLD, source) for source in FETCHED_ABSTRACT_SOURCES}


def entry_doi(entry):
    """Normalized DOI from an explicit `doi` field, the link, or the RSS id (None for PII-only)."""
    from paper_feed.identity import normalize_doi
    for value in (entry.get('doi'), entry.get('link'), entry.get('id')):
        doi = normalize_doi(value) if value else None
        if doi:
            return doi
    doi = extract_doi(entry.get('link') or '', entry.get('id') or '')
    return doi if _usable_doi(doi) else None


PII_BATCH_SIZE = 20


def resolve_dois_from_piis(piis, breaker=None):
    """{PII (upper-case): DOI} for Elsevier PIIs via Crossref's alternative-id filter.

    ScienceDirect links carry only a PII.  Repeated filters are OR-ed, so one
    request resolves up to PII_BATCH_SIZE PIIs.  A PII matched by more than
    one work is ambiguous and dropped.  Never raises.
    """
    from paper_feed.identity import normalize_doi
    wanted = list(dict.fromkeys(str(pii).upper() for pii in piis if pii))
    resolved, seen = {}, {}
    for start in range(0, len(wanted), PII_BATCH_SIZE):
        chunk = wanted[start:start + PII_BATCH_SIZE]
        data = _abstract_api_get("https://api.crossref.org/works", "Crossref", breaker=breaker, params={
            "filter": ",".join(f"alternative-id:{pii}" for pii in chunk),
            "rows": len(chunk) * 2, "select": "DOI,alternative-id"})
        for item in ((data or {}).get("message") or {}).get("items") or []:
            doi = normalize_doi(item.get("DOI"))
            for alt in item.get("alternative-id") or []:
                key = str(alt).upper()
                if doi and key in chunk:
                    seen[key] = seen.get(key, 0) + 1
                    resolved[key] = doi
    return {pii: doi for pii, doi in resolved.items() if seen.get(pii) == 1}


def resolve_doi_from_pii(pii, breaker=None):
    """DOI for one Elsevier PII (see resolve_dois_from_piis).  Never raises."""
    return resolve_dois_from_piis([pii], breaker=breaker).get(str(pii or "").upper())


def fetch_abstract_with_fallback(entry, api_key=None, base_url=None, proxy=None, *,
                                 mailto=None, breakers=None, allow_title_search=False):
    """Free (token-less) abstract lookup: Crossref -> OpenAlex -> Semantic Scholar.

    Returns ``(abstract, source, raw_abstract)`` with source in
    FETCHED_ABSTRACT_SOURCES, or ``(None, None, None)``.  Never raises.
    ``api_key``/``base_url``/``proxy`` are accepted for backward compatibility
    only: this function never calls the AI model.  A title-only Semantic
    Scholar search can match the wrong paper, so it is opt-in.
    """
    try:
        title = entry.get('title', '')
        doi = entry_doi(entry)
        breakers = breakers or {}
        lookups = []
        if doi:
            lookups = [] if is_elsevier_doi(doi) else [
                ('crossref', lambda: get_abstract_from_crossref(doi, breaker=breakers.get('crossref')))]
            lookups += [
                ('openalex', lambda: get_abstract_from_openalex(doi, mailto=mailto, breaker=breakers.get('openalex'))),
                ('semantic_scholar', lambda: get_abstract_from_semantic_scholar(title, doi=doi, breaker=breakers.get('semantic_scholar'))),
            ]
        elif allow_title_search and title:
            lookups = [('semantic_scholar', lambda: get_abstract_from_semantic_scholar(title, breaker=breakers.get('semantic_scholar')))]
        for source, lookup in lookups:
            try:
                abstract = lookup()
            except Exception as e:  # defensive: lookups already swallow errors
                print(f"  {source} lookup failed: {e}")
                abstract = None
            if abstract and len(abstract) >= ABSTRACT_MIN_LENGTH:
                print(f"  [OK] Got abstract from {source} ({len(abstract)} chars)")
                return abstract, source, abstract
    except Exception as e:
        print(f"  Abstract lookup failed: {type(e).__name__}: {e}")
    return None, None, None

# ----------------

def load_translations():
    if os.path.exists(TRANSLATIONS_CACHE):
        try:
            with open(TRANSLATIONS_CACHE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_translations(cache):
    atomic_write(TRANSLATIONS_CACHE, json.dumps(cache, ensure_ascii=False, indent=2))

def load_categories():
    if os.path.exists(CATEGORIES_FILE):
        try:
            with open(CATEGORIES_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading categories file: {e}")
    return {}

def load_user_corrections():
    if os.path.exists(USER_CORRECTIONS_FILE):
        try:
            with open(USER_CORRECTIONS_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            print(f"Error reading user corrections: {e}")
    return {}

def load_abstracts():
    """加载摘要缓存"""
    if os.path.exists(ABSTRACTS_CACHE):
        try:
            with open(ABSTRACTS_CACHE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_abstracts(cache):
    """保存摘要缓存"""
    atomic_write(ABSTRACTS_CACHE, json.dumps(cache, ensure_ascii=False, indent=2))

def normalize_label_entries(raw_entries, valid_names=None):
    entries = []
    if isinstance(raw_entries, dict):
        raw_entries = [raw_entries]
    if isinstance(raw_entries, str):
        raw_entries = [{"name": raw_entries, "confidence": 0.6}]
    if not isinstance(raw_entries, list):
        return []
    for entry in raw_entries:
        if isinstance(entry, str):
            name = entry.strip()
            confidence = 0.6
        elif isinstance(entry, dict):
            name = str(entry.get("name", "")).strip()
            try:
                confidence = float(entry.get("confidence", 0.6))
            except Exception:
                confidence = 0.6
        else:
            continue
        if not name:
            continue
        if valid_names and name not in valid_names:
            continue
        confidence = max(0.0, min(1.0, confidence))
        entries.append({"name": name, "confidence": confidence})
    entries.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return entries

def pick_primary(entries, fallback=""):
    if entries:
        return entries[0].get("name", "") or fallback
    return fallback

def _result_index(result, size):
    """1-based `index` echoed by the model, or None when missing/invalid."""
    if not isinstance(result, dict):
        return None
    value = result.get("index")
    if isinstance(value, bool):
        return None
    try:
        index = int(str(value).strip().rstrip("."))
    except (TypeError, ValueError):
        return None
    return index if 1 <= index <= size else None


def align_batch_results(chunk, result_list):
    """Pair titles with model results; returns [(title, result)] for aligned items only.

    Results are matched by their echoed 1-based ``index``.  Old-format
    responses without indexes are accepted only when the count matches (then
    order is trusted).  Items that cannot be aligned are dropped so their
    titles stay stale and are retried on the next run.
    """
    size = len(chunk)
    results = list(result_list or [])
    indexes = [_result_index(result, size) for result in results]
    positional_ok = len(results) == size and all(
        index is None or index == position + 1 for position, index in enumerate(indexes))
    if positional_ok:
        return list(zip(chunk, results))
    aligned = {}
    for result, index in zip(results, indexes):
        if index is not None and index not in aligned:
            aligned[index] = result
    return [(chunk[index - 1], aligned[index]) for index in sorted(aligned)]


def batch_analyze_papers(titles, api_key, base_url=None, proxy=None, model=None, report=None):
    """Translate/classify titles.  Returns {title: analysis}.

    Failures never raise: when *report* is a dict, ``report["failed"]`` is
    incremented by the number of titles that could not be analysed and short
    messages are appended to ``report["errors"]``.
    """
    if report is not None:
        report.setdefault("failed", 0)
        report.setdefault("errors", [])
    if not titles or not api_key:
        return {}

    codex = is_codex_backend(api_key)
    if proxy and not codex:
        print(f"Using proxy: {proxy}")
    try:
        client = make_openai_client(api_key, base_url, proxy)
    except Exception as e:
        # A bad proxy URL or missing dependency must not abort the RSS refresh.
        print(f"Could not create AI client; skipping AI analysis: {e}")
        if report is not None:
            report["failed"] += len(titles)
        add_error(report, f"AI client error: {type(e).__name__}: {e}")
        return {}
    # Codex CLI ignores the OpenAI model name; show the model that really runs.
    model = api_key.model if codex else (model or DEFAULT_OPENAI_MODEL)

    analysis_results = {}
    # Each `codex exec` costs a large fixed prompt overhead: fewer, larger calls.
    chunk_size = CODEX_ANALYSIS_CHUNK_SIZE if codex else OPENAI_ANALYSIS_CHUNK_SIZE
    chunks = [titles[i:i + chunk_size] for i in range(0, len(titles), chunk_size)]

    categories = load_categories() or {}
    domain = categories.get("domain") if isinstance(categories.get("domain"), str) else ""
    domain = domain.strip() or DEFAULT_CLASSIFICATION_DOMAIN
    method_defs = categories.get("methods", [])
    topic_defs = categories.get("topics", [])
    theory_defs = categories.get("theories", [])
    context_defs = categories.get("contexts", [])
    subject_defs = categories.get("subjects", [])

    method_names = [m.get("name") for m in method_defs if isinstance(m, dict) and m.get("name")]
    topic_names = [t.get("name") for t in topic_defs if isinstance(t, dict) and t.get("name")]
    if not method_names:
        method_names = ["Experiment", "Archival", "Theoretical", "Review", "Qualitative"]
    if not topic_names:
        topic_names = ["Other Marketing"]
    theory_names = [t for t in theory_defs if isinstance(t, str)]
    context_names = [t for t in context_defs if isinstance(t, str)]
    subject_names = [t for t in subject_defs if isinstance(t, str)]

    methods_text = "\n".join([f"- {m.get('name')}: {', '.join(m.get('keywords', [])[:6])}" for m in method_defs if isinstance(m, dict)])
    topics_text = "\n".join([f"- {t.get('name')}: {', '.join(t.get('keywords', [])[:8])}" for t in topic_defs if isinstance(t, dict)])

    method_fallback = fallback_label(method_names, "Qualitative")
    topic_fallback = fallback_label(topic_names, "Other Marketing")

    breaker = CircuitBreaker(OPENAI_BREAKER_THRESHOLD, "Codex CLI" if codex else "OpenAI endpoint")

    def analyze_chunk(chunk):
        """Return (pairs, error_message, failed_count); error_message is None on full success."""
        prompt = f"""You are a research classification expert in {domain}.
For each paper title, provide:
0. "index": the number of the title in the input list (1-based), copied exactly.
1. "zh": Chinese translation (academic style). DO NOT use any HTML tags or angle brackets.
2. "methods": 1-2 items, each with {{ "name": <method>, "confidence": 0-1 }}.
3. "topics": 1-3 items, each with {{ "name": <topic>, "confidence": 0-1 }}.
4. "theories": optional array (use known theories if implied).
5. "context": optional array of research context tags.
6. "subjects": optional array of research subjects.
7. "novelty_score": optional integer 1-5 (only if clearly implied by title).

Use ONLY the following method names: {method_names}
Use ONLY the following topic names: {topic_names}
Theory tags (optional): {theory_names}
Context tags (optional): {context_names}
Research subjects (optional): {subject_names}

Method hints:
{methods_text}

Topic hints:
{topics_text}

Rules:
- Output must be valid JSON.
- If uncertain, choose broader topics and keep confidence low (<=0.6).
- Return exactly one result per title, in the original order, each with its "index".

Example:
{{ "results": [{{ "index": 1, "zh": "示例标题", "methods": [{{"name": "{method_names[0] if method_names else 'Experiment'}", "confidence": 0.8}}], "topics": [{{"name": "{topic_names[0] if topic_names else 'Other Marketing'}", "confidence": 0.7}}], "theories": [], "context": [], "subjects": [], "novelty_score": null }}] }}
"""
        
        user_content = "Titles:\n" + "\n".join([f"{j+1}. {t}" for j, t in enumerate(chunk)])

        try:
            response = chat_completion_with_retry(
                client,
                breaker=breaker,
                model=model,
                messages=[
                    {"role": "system", "content": "You are a JSON-only API. You must return valid JSON."},
                    {"role": "user", "content": prompt + "\n\n" + user_content}
                ],
                response_format={"type": "json_object"}
            )
            data = parse_model_json(response.choices[0].message.content or "")
            result_list = data.get("results", []) if isinstance(data, dict) else []
            if not isinstance(result_list, list):
                result_list = []

            pairs = align_batch_results(chunk, result_list)
            missing = len(chunk) - len(pairs)
            if missing:
                # Aligned items are kept; the rest stay stale for the next run.
                message = (f"GPT returned {len(result_list)} items for {len(chunk)} titles; "
                           f"kept {len(pairs)}, {missing} left for the next run")
                print(f"Warning: {message}.")
                return pairs, message, missing
            return pairs, None, 0
        except Exception as e:
            print(f"Analysis error for chunk: {e}")
            return [], f"{type(e).__name__}: {e}", len(chunk)

    worker_count = min(CODEX_ANALYSIS_WORKERS if codex else AI_ANALYSIS_WORKERS, len(chunks))
    print(f"Starting concurrent analysis with {worker_count} workers for {len(chunks)} chunks (model: {model})...")
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        future_to_chunk = {executor.submit(analyze_chunk, chunk): chunk for chunk in chunks}

        completed = 0
        for future in as_completed(future_to_chunk):
            try:
                chunk_results, chunk_error, chunk_failed = future.result()
            except Exception as e:
                chunk_results, chunk_error, chunk_failed = [], f"{type(e).__name__}: {e}", len(future_to_chunk[future])
            if chunk_error:
                if report is not None:
                    report["failed"] += chunk_failed
                add_error(report, chunk_error)
            valid_methods = set(method_names)
            valid_topics = set(topic_names)
            for original_title, data in chunk_results:
                if not isinstance(data, dict):
                    data = {}
                methods = normalize_label_entries(data.get("methods", data.get("method", "")), valid_methods)
                topics = normalize_label_entries(data.get("topics", data.get("topic", "")), valid_topics)
                if not methods:
                    methods = [{"name": method_fallback, "confidence": 0.4}]
                if not topics:
                    topics = [{"name": topic_fallback, "confidence": 0.4}]
                analysis_results[original_title] = {
                    "zh": data.get("zh", original_title),
                    "methods": methods,
                    "topics": topics,
                    "theories": [t for t in (data.get("theories") or []) if isinstance(t, str)],
                    "context": [t for t in (data.get("context") or []) if isinstance(t, str)],
                    "subjects": [t for t in (data.get("subjects") or []) if isinstance(t, str)],
                    "novelty_score": data.get("novelty_score"),
                    "classification_version": CLASSIFICATION_VERSION
                }
            
            completed += 1
            if completed % 5 == 0 or completed == len(chunks):
                print(f"Progress: {completed}/{len(chunks)} chunks analyzed...")
            
    return analysis_results

def _config_lines(lines):
    """Strip blank lines and whole-line `#` comments."""
    return [line.strip() for line in lines if line.strip() and not line.strip().startswith('#')]


def load_config(filename, env_var_name=None):
    """Load a line-based list (journals.dat / keywords.dat).

    A non-empty environment variable (e.g. RSS_KEYWORDS in GitHub Actions)
    overrides the file entirely; lines are separated by newlines or `;`.
    """
    if env_var_name and os.environ.get(env_var_name, "").strip():
        content = os.environ[env_var_name]
        separator = '\n' if '\n' in content else ';'
        values = _config_lines(content.split(separator))
        print(f"NOTE: environment variable {env_var_name} is set and OVERRIDES "
              f"{os.path.basename(filename)} ({len(values)} entries). Unset it to use the file.")
        return values

    if os.path.exists(filename):
        print(f"Loading config from local file: {filename}")
        with open(filename, 'r', encoding='utf-8') as f:
            return _config_lines(f)

    return []


# --- Keyword rules -------------------------------------------------------------
# One rule per line; lines are OR'd.  Inside a line, `AND` (any case, whole word)
# joins terms that must all match.  A term prefixed with `-` or `NOT ` must NOT
# match.  Terms are case-insensitive and anchored at a word start (`\bterm`), so
# `ai` does not match "said" while `consum` still matches "consumer".  Wrap a
# phrase in double quotes to keep a literal "and" inside it.  Lines starting
# with `#` are comments.  A line with only exclusions matches nothing.
_AND_SPLIT = re.compile(r'\s+AND\s+', re.IGNORECASE)
_NEGATION = re.compile(r'^(?:-\s*|NOT\s+)', re.IGNORECASE)


def _split_rule_terms(rule):
    """Split on AND, ignoring any AND inside double-quoted phrases."""
    quoted = []

    def stash(match):
        quoted.append(match.group(0))
        return f"\x00{len(quoted) - 1}\x00"

    masked = re.sub(r'"[^"]*"', stash, rule)
    parts = _AND_SPLIT.split(masked)
    restored = [re.sub(r'\x00(\d+)\x00', lambda m: quoted[int(m.group(1))], part) for part in parts]
    return [part.strip() for part in restored if part.strip()]


def _term_pattern(term):
    words = term.split()
    body = r'\s+'.join(re.escape(word) for word in words)
    # Anchor ASCII terms at a word start; CJK text has no word boundaries.
    prefix = r'(?<![A-Za-z0-9_])' if re.match(r'[A-Za-z0-9_]', term) else ''
    return re.compile(prefix + body, re.IGNORECASE)


@functools.lru_cache(maxsize=512)
def parse_keyword_rule(rule):
    """Return {"include": [(term, regex)], "exclude": [(term, regex)]} for one line."""
    include, exclude = [], []
    text = (rule or "").strip()
    if not text or text.startswith('#'):
        return {"include": [], "exclude": []}
    for raw_term in _split_rule_terms(text):
        negated = False
        term = raw_term
        match = _NEGATION.match(term)
        if match and len(term) > match.end():
            negated = True
            term = term[match.end():].strip()
        term = term.strip().strip('"').strip()
        if not term:
            continue
        (exclude if negated else include).append((term, _term_pattern(term)))
    return {"include": include, "exclude": exclude}


def keyword_rules(queries):
    return [parse_keyword_rule(query) for query in (queries or []) if query and query.strip()]


def match_text(text, queries):
    """True when any rule has all include terms and none of its exclude terms."""
    text = text or ""
    for rule in keyword_rules(queries):
        if not rule["include"]:
            continue
        if all(pattern.search(text) for _, pattern in rule["include"]) and \
                not any(pattern.search(text) for _, pattern in rule["exclude"]):
            return True
    return False


def keyword_display_terms(queries):
    """Positive terms for display (feed.json `keywords`)."""
    terms = []
    for rule in keyword_rules(queries):
        terms.extend(term for term, _ in rule["include"])
    return sorted(set(terms), key=str.lower)


def preview_keywords(queries, papers, sample_limit=10):
    """Evaluate keyword rules against stored papers ({paper_id, title, text})."""
    rules = keyword_rules(queries)
    term_patterns = {}
    for rule in rules:
        for term, pattern in rule["include"]:
            term_patterns.setdefault(term, pattern)
    term_counts = {term: 0 for term in term_patterns}
    matched = 0
    samples = []
    for paper in papers:
        text = paper.get("text") or paper.get("title") or ""
        for term, pattern in term_patterns.items():
            if pattern.search(text):
                term_counts[term] += 1
        if match_text(text, queries):
            matched += 1
            if len(samples) < sample_limit:
                samples.append({"paper_id": paper.get("paper_id"), "title": paper.get("title") or ""})
    return {
        "total_papers": len(papers),
        "matched": matched,
        "terms": [{"term": term, "count": count} for term, count in term_counts.items()],
        "samples": samples,
    }

def strip_tags(text):
    """移除所有 HTML 标签和尖括号内容"""
    if not text:
        return ""
    # Replace common block tag endings with spaces to keep fields separated.
    text = re.sub(r'</(p|div|br|li|ul|ol|h[1-6]|table|tr|td|th)\s*>', ' ', text, flags=re.IGNORECASE)
    # Remove remaining HTML tags like <em>, <strong>
    text = re.sub(r'<[^>]+>', '', text)
    return re.sub(r'\s+', ' ', text).strip()

def extract_metadata_summary(summary):
    """保留摘要中的元数据行（发布日期/来源/作者），去掉正文内容"""
    if not summary:
        return ""
    clean = strip_tags(summary)
    if not clean:
        return ""

    clean = re.sub(r'\s+', ' ', clean).strip()
    clean = re.sub(r'([^\s])\s*(Publication date|Source|Authors?\(s\)?)(\s*:\s*)', r'\1\n\2:', clean, flags=re.IGNORECASE)
    clean = re.sub(r'\s*(Publication date|Source|Authors?\(s\)?)(\s*:\s*)', r'\n\1:', clean, flags=re.IGNORECASE)

    lines = [line.strip() for line in re.split(r'\n+', clean) if line.strip()]
    keep = []
    for line in lines:
        if re.match(r'^(Publication date|Source|Authors?\(s\)?)\s*:', line, flags=re.IGNORECASE):
            keep.append(re.sub(r':\s*', ': ', line, count=1).strip())
    return " ".join(keep)

def normalize_journal_title(journal):
    if not journal:
        return ""
    clean = journal.strip()
    if clean.lower() == "latest results":
        return "Journal of the Academy of Marketing Science"
    prefix_patterns = [
        r'^sciencedirect(?:\s+publication)?\s*[:\-]\s*',
        r'^wiley\s*[:\-]\s*',
        r'^sage publications inc\s*[:\-]\s*',
        r'^sage publications ltd\s*[:\-]\s*',
        r'^tandf\s*[:\-]\s*',
        r'^iorms\s*[:\-]\s*',
        r'^academy of management\s*[:\-]\s*',
        r'^the university of chicago press\s*[:\-]\s*'
    ]
    suffix_patterns = [
        r'\s*[:\-]?\s*table of contents\s*$',
        r'\s*[:\-]?\s*advance access\s*$',
        r'\s*[:\-]?\s*latest results\s*$',
        r'\s*[:\-]?\s*vol(?:ume)?\s*\d+\s*,?\s*iss(?:ue)?\.?\s*\d+\s*$',
        r'\s*[:\-]?\s*vol(?:ume)?\s*\d+\s*$',
        r'\s*[:\-]?\s*iss(?:ue)?\.?\s*\d+\s*$'
    ]

    changed = True
    while changed:
        changed = False
        for pattern in prefix_patterns:
            next_clean = re.sub(pattern, '', clean, flags=re.IGNORECASE)
            if next_clean != clean:
                clean = next_clean
                changed = True

        for pattern in suffix_patterns:
            next_clean = re.sub(pattern, '', clean, flags=re.IGNORECASE)
            if next_clean != clean:
                clean = next_clean
                changed = True

    clean = re.sub(r'\s+', ' ', clean).strip()
    return clean

def normalize_paper_title(title, journal=None):
    if not title:
        return ""
    clean = title.strip()
    match = re.match(r'^\[(.*?)\]\s*(.+)$', clean)
    if not match:
        return clean
    bracket = match.group(1).strip()
    remainder = match.group(2).strip()
    
    bracket_lower = bracket.lower()
    if "sciencedirect publication" in bracket_lower:
        return remainder
    if "nature.com subject feeds" in bracket_lower:
        return remainder
    if "table of contents" in bracket_lower:
        return remainder
        
    if journal:
        if bracket == journal or bracket == normalize_journal_title(journal):
            return remainder
    return clean

def remove_illegal_xml_chars(text):
    """
    移除 XML 1.0 不支持的 ASCII 控制字符 (Char value 0-8, 11-12, 14-31)
    """
    if not text:
        return ""
    # 正则表达式：匹配 ASCII 0-8, 11, 12, 14-31 这些控制字符
    # \x09是tab, \x0a是换行, \x0d是回车，这些是合法的，所以不删
    illegal_chars = r'[\x00-\x08\x0b\x0c\x0e-\x1f]'
    return re.sub(illegal_chars, '', text)

def convert_struct_time_to_datetime(struct_time):
    if not struct_time:
        return datetime.datetime.now()
    return datetime.datetime.fromtimestamp(time.mktime(struct_time))

def fetch_rss_result(rss_url, retries=3):
    """Fetch one source and retain enough status information for job reporting."""
    print(f"Fetching: {rss_url}...")
    retries = max(1, retries)
    last_error = None
    last_status = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(
                rss_url,
                headers={"User-Agent": "Paper-Feed/1.0 (+https://github.com/Xiaotian-Liu-MKT/paper-feed)"},
                timeout=RSS_REQUEST_TIMEOUT,
            )
            last_status = getattr(response, "status_code", None)
            if last_status is not None and not 200 <= last_status < 300:
                last_error = f"HTTP {last_status}"
                # Client errors (including 404) will not improve with a retry.
                retryable = last_status == 429 or last_status >= 500
                print(f"Error fetching {rss_url} (attempt {attempt}/{retries}): {last_error}")
                if not retryable:
                    break
                if attempt < retries:
                    time.sleep(2 ** (attempt - 1))
                continue

            feed = feedparser.parse(response.content)
            entries = []
            journal_title = feed.feed.get('title', 'Unknown Journal')
            
            for entry in feed.entries:
                pub_struct = entry.get('published_parsed', entry.get('updated_parsed'))
                pub_date = convert_struct_time_to_datetime(pub_struct)
                
                summary_raw = entry.get('summary', entry.get('description', ''))
                entries.append({
                    'title': entry.get('title', ''),
                    'link': entry.get('link', ''),
                    'pub_date': pub_date,
                    'summary': summary_raw,
                    'journal': journal_title,
                    'id': entry.get('id', entry.get('link', ''))
                })
            return {
                "url": rss_url,
                "success": True,
                "entries": entries,
                "status_code": last_status,
                "attempts": attempt,
                "error": None,
            }
        except (requests.RequestException, OSError) as e:
            last_error = str(e)
            print(f"Error fetching {rss_url} (attempt {attempt}/{retries}): {e}")
        except Exception as e:
            last_error = str(e)
            print(f"Error parsing {rss_url} (attempt {attempt}/{retries}): {e}")
        if attempt < retries:
            time.sleep(2 ** (attempt - 1))
    return {
        "url": rss_url,
        "success": False,
        "entries": [],
        "status_code": last_status,
        "attempts": attempt,
        "error": last_error or "unknown fetch failure",
    }


def parse_rss(rss_url, retries=3):
    """Backward-compatible entry-only interface for callers outside the job flow."""
    return fetch_rss_result(rss_url, retries=retries)["entries"]

def get_existing_items():
    # (保持不变，但增加容错：如果 XML 坏了，就返回空列表重新抓)
    if not os.path.exists(OUTPUT_FILE):
        return []
    
    print(f"Loading existing items from {OUTPUT_FILE}...")
    try:
        feed = feedparser.parse(OUTPUT_FILE)
        # 如果解析出错（比如现在的 invalid char），feedparser 可能会拿到空或者 bozo 标志
        if hasattr(feed, 'bozo') and feed.bozo == 1:
             print("Warning: Existing XML file might be corrupted. Ignoring old items.")
             # 这里可以选择 return [] 直接丢弃坏掉的旧数据，重新开始
             # return [] 
             # 或者尝试读取能读的部分（取决于损坏位置）
        
        entries = []
        for entry in feed.entries:
            pub_struct = entry.get('published_parsed')
            pub_date = convert_struct_time_to_datetime(pub_struct)

            entries.append({
                'title': entry.get('title', ''),
                'link': entry.get('link', ''),
                'pub_date': pub_date,
                'summary': entry.get('summary', ''),
                'journal': entry.get('author', ''),
                'id': entry.get('id', entry.get('link', '')),
                'is_old': True
            })
        return entries
    except Exception as e:
        print(f"Error reading existing file: {e}")
        return [] # 如果旧文件读不了，就当做第一次运行

def match_entry(entry, queries):
    """Apply keyword rules (see parse_keyword_rule) to an entry's title + summary."""
    text_to_search = (entry.get('title') or '') + " " + (entry.get('summary') or '')
    return match_text(text_to_search, queries)

def generate_rss_xml(items, queries):
    """生成 RSS 2.0 XML 文件 (已加入非法字符清洗)"""
    # RSS jobs hand this function durable DB records.  Keep the legacy signature
    # for callers and tests, but never rebuild job history from XML/cache files.
    if items and items[0].get("paper_id"):
        export_items(items, OUTPUT_FILE, FEED_JSON, keyword_display_terms(queries), limit=MAX_ITEMS, atomic_write=atomic_write)
        print(f"Successfully generated {OUTPUT_FILE} with {min(len(items), MAX_ITEMS)} items.")
        return
    rss_items = []
    
    items.sort(key=lambda x: x['pub_date'], reverse=True)
    items = items[:MAX_ITEMS]

    write_feed_json(items, queries)
    
    for item in items:
        raw_title = item['title']
        raw_journal = item['journal']
        clean_journal = normalize_journal_title(raw_journal)
        clean_title = normalize_paper_title(raw_title, raw_journal)

        title = clean_title
            
        # --- 关键修改：清洗数据 ---
        clean_title = remove_illegal_xml_chars(title)
        clean_summary = remove_illegal_xml_chars(extract_metadata_summary(item.get('summary', '')))
        clean_journal = remove_illegal_xml_chars(clean_journal)
        # -----------------------

        rss_item = Item(
            title = clean_title,
            link = item['link'],
            description = clean_summary,
            author = clean_journal,
            guid = Guid(item['id']),
            pubDate = item['pub_date']
        )
        rss_items.append(rss_item)

    feed = Feed(
        title = "My Customized Papers",
        link = "https://github.com/your_username/your_repo",
        description = "Aggregated research papers",
        language = "en-US",
        lastBuildDate = datetime.datetime.now(),
        items = rss_items
    )

    atomic_write(OUTPUT_FILE, feed.rss())
    print(f"Successfully generated {OUTPUT_FILE} with {len(rss_items)} items.")

def write_feed_json(items, queries):
    os.makedirs(WEB_DIR, exist_ok=True)

    # 加载配置
    config = get_config()
    ai = ai_settings(config, log=True)
    api_key = ai["api_key"]
    base_url = ai["base_url"]
    proxy = ai["proxy"]

    # 加载已有的翻译缓存
    translation_cache = load_translations()
    categories = load_categories() or {}
    valid_methods = {m.get("name") for m in categories.get("methods", []) if isinstance(m, dict) and m.get("name")}
    valid_topics = {t.get("name") for t in categories.get("topics", []) if isinstance(t, dict) and t.get("name")}
    user_corrections = load_user_corrections()

    # 收集需要翻译/分析的新标题
    titles_to_analyze = []
    for item in items:
        raw_title = item['title']
        # If title not in cache, OR if cache entry is old/incomplete, re-analyze
        if raw_title not in translation_cache:
            if api_key:
                titles_to_analyze.append(raw_title)
        else:
            # Check if it needs upgrade (is string OR is dict but missing 'topic')
            cache_val = translation_cache[raw_title]
            needs_upgrade = False
            if isinstance(cache_val, str):
                needs_upgrade = True
            elif isinstance(cache_val, dict):
                cached_methods = normalize_label_entries(
                    cache_val.get("methods", cache_val.get("method", "")),
                    valid_methods
                )
                cached_topics = normalize_label_entries(
                    cache_val.get("topics", cache_val.get("topic", "")),
                    valid_topics
                )
                if not cached_methods or not cached_topics:
                    needs_upgrade = True
                if cache_val.get("classification_version") != CLASSIFICATION_VERSION:
                    needs_upgrade = True
            if needs_upgrade and api_key:
                titles_to_analyze.append(raw_title)

    if not api_key:
        print(f"AI analysis skipped: {ai_skip_message(ai)}.")

    # 执行分析
    if titles_to_analyze:
        print(f"Analyzing {len(titles_to_analyze)} papers (Translation + Classification) with {ai_backend_label(ai)}...")
        new_results = batch_analyze_papers(titles_to_analyze, api_key, base_url, proxy, model=ai["model"])
        if new_results:
            translation_cache.update(new_results)
            save_translations(translation_cache)

    # 加载摘要缓存
    abstract_cache = load_abstracts()

    data = []
    for item in items:
        raw_title = item['title']
        raw_journal = item['journal']
        clean_journal = normalize_journal_title(raw_journal)
        clean_title = normalize_paper_title(raw_title, raw_journal)

        display_title = clean_title

        # 获取缓存的摘要
        item_id = item['id']
        abstract_info = abstract_cache.get(item_id, {})
        abstract_text = abstract_info.get('abstract', '')
        raw_abstract_text = abstract_info.get('raw_abstract', '')
        abstract_source = abstract_info.get('source', '')

        # Get cached analysis data (support both old string format and new dict format)
        cache_data = translation_cache.get(raw_title, "")
        title_zh = ""
        methods = []
        topics = []
        theories = []
        context = []
        subjects = []
        novelty_score = None
        classification_version = ""
        classification_source = "gpt"
        user_corrected = False

        if isinstance(cache_data, dict):
            title_zh = cache_data.get("zh", "")
            methods = normalize_label_entries(cache_data.get("methods", cache_data.get("method", "")), valid_methods)
            topics = normalize_label_entries(cache_data.get("topics", cache_data.get("topic", "")), valid_topics)
            theories = [t for t in (cache_data.get("theories") or []) if isinstance(t, str)]
            context = [t for t in (cache_data.get("context") or []) if isinstance(t, str)]
            subjects = [t for t in (cache_data.get("subjects") or []) if isinstance(t, str)]
            novelty_score = cache_data.get("novelty_score")
            classification_version = cache_data.get("classification_version", "")
        else:
            title_zh = cache_data  # Old string format

        # Apply user corrections by ID (highest priority)
        correction = user_corrections.get(item_id, {})
        if isinstance(correction, dict) and correction:
            corrected_methods = normalize_label_entries(correction.get("methods", []), valid_methods)
            corrected_topics = normalize_label_entries(correction.get("topics", []), valid_topics)
            if corrected_methods:
                methods = corrected_methods
            if corrected_topics:
                topics = corrected_topics
            if isinstance(correction.get("theories"), list):
                theories = [t for t in correction.get("theories") if isinstance(t, str)]
            if isinstance(correction.get("context"), list):
                context = [t for t in correction.get("context") if isinstance(t, str)]
            if isinstance(correction.get("subjects"), list):
                subjects = [t for t in correction.get("subjects") if isinstance(t, str)]
            if correction.get("novelty_score") is not None:
                novelty_score = correction.get("novelty_score")
            classification_source = "user"
            user_corrected = True

        primary_method = pick_primary(methods, fallback_label(valid_methods, "Qualitative"))
        primary_topic = pick_primary(topics, fallback_label(valid_topics, "Other Marketing"))

        data.append({
            "paper_id": item.get("paper_id"),
            "id": item['id'],
            "title": strip_tags(remove_illegal_xml_chars(display_title)),
            "title_zh": strip_tags(title_zh),
            "method": primary_method,
            "topic": primary_topic,
            "methods": methods,
            "topics": topics,
            "theories": theories,
            "context": context,
            "subjects": subjects,
            "novelty_score": novelty_score,
            "classification_source": classification_source,
            "classification_version": classification_version,
            "user_corrected": user_corrected,
            "link": item['link'],
            "summary": strip_tags(remove_illegal_xml_chars(extract_metadata_summary(item.get('summary', '')))),
            "abstract": remove_illegal_xml_chars(abstract_text),
            "raw_abstract": remove_illegal_xml_chars(raw_abstract_text),
            "abstract_source": abstract_source,
            "journal": remove_illegal_xml_chars(clean_journal),
            "pub_date": item['pub_date'].isoformat()
        })

    keywords = keyword_display_terms(queries)

    payload = {
        "generated_at": datetime.datetime.now().isoformat(),
        "keywords": keywords,
        "items": data
    }

    atomic_write(FEED_JSON, json.dumps(payload, ensure_ascii=True, indent=2))
    print(f"Generated {FEED_JSON} with {len(data)} items.")

def compute_journal_hash(journals):
    content = "\n".join(journals).strip() + "\n"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def stale_analysis_items(items):
    """Durable items whose title analysis is missing or from an older classifier."""
    return [item for item in items if not isinstance(item.get("translation"), dict)
            or item["translation"].get("classification_version") != CLASSIFICATION_VERSION]


def analyze_database_items(database, items, config=None, report=None):
    """Run GPT only for missing/stale durable translations and store by paper_id.

    Returns the number of saved analyses; failures are added to *report*.
    """
    if report is not None:
        report.setdefault("failed", 0)
        report.setdefault("errors", [])
    config = get_config() if config is None else config
    stale = stale_analysis_items(items)
    if not stale:
        return 0
    ai = ai_settings(config, log=True)
    if not ai["ready"]:
        print(f"AI analysis skipped for {len(stale)} papers: {ai_skip_message(ai)}.")
        return 0
    titles = list(dict.fromkeys(item["title"] for item in stale))
    print(f"Analyzing {len(titles)} title(s) with {ai_backend_label(ai)}...")
    results = batch_analyze_papers(titles, ai["api_key"], ai["base_url"], ai["proxy"],
                                   model=ai["model"], report=report) or {}
    durable = {item["paper_id"]: results[item["title"]] for item in stale if item["title"] in results}
    return save_db_translations(database, durable)

def _database_path():
    return ensure_database(BASE_DIR, os.environ.get("PAPER_FEED_DB") or None)


def locked_flow(kind):
    """Run a mutating flow under the cross-process ``job_lock`` (raises LockBusyError).

    The lock is re-entrant within a thread, so a locked flow may call another.
    """
    def decorate(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            with job_lock(kind):
                return func(*args, **kwargs)
        return wrapper
    return decorate


@locked_flow("refresh")
def run_rss_flow():
    # 请确保这里的调用参数与你目前的 secrets 配置一致
    rss_urls = load_config(JOURNALS_FILE, 'RSS_JOURNALS')
    queries = load_config(KEYWORDS_FILE, 'RSS_KEYWORDS')

    if not rss_urls or not queries:
        missing = " and ".join(name for name, values in (("journals.dat", rss_urls), ("keywords.dat", queries)) if not values)
        message = f"Configuration is empty or missing: {missing} (or RSS_JOURNALS/RSS_KEYWORDS)."
        print(f"Error: {message}")
        return {"status": "error", "message": message, "config_error": True,
                "successful_sources": [], "failed_sources": [], "new_items": 0, "published": False,
                "failed": 0, "errors": [message]}

    os.makedirs(WEB_DIR, exist_ok=True)
    journal_hash = compute_journal_hash(rss_urls)
    prev_hash = None
    if os.path.exists(JOURNAL_HASH_FILE):
        with open(JOURNAL_HASH_FILE, "r", encoding="utf-8") as f:
            prev_hash = f.read().strip()

    if prev_hash and prev_hash != journal_hash:
        print("Journal list changed. Keeping cached history; only future updates are affected.")

    print("Starting RSS fetch from remote...")
    with ThreadPoolExecutor(max_workers=min(RSS_FETCH_WORKERS, len(rss_urls))) as executor:
        futures = {executor.submit(fetch_rss_result, url): index for index, url in enumerate(rss_urls)}
        fetched_by_index = [None for _ in rss_urls]
        for future in as_completed(futures):
            index = futures[future]
            try:
                fetched_by_index[index] = future.result()
            except Exception as e:
                print(f"Unexpected RSS worker failure for {rss_urls[index]}: {e}")
                fetched_by_index[index] = {
                    "url": rss_urls[index], "success": False, "entries": [],
                    "status_code": None, "attempts": 0, "error": str(e),
                }

    successful_sources = [result["url"] for result in fetched_by_index if result and result["success"]]
    failed_sources = [result["url"] for result in fetched_by_index if not result or not result["success"]]
    report = {"failed": 0, "errors": []}
    for result in fetched_by_index:
        if not result or not result["success"]:
            add_error(report, f"RSS source failed: {(result or {}).get('url')} ({(result or {}).get('error') or 'unknown error'})")
    # Bootstrap only when no local DB exists (notably GitHub Actions), then log
    # every fetch outcome and ingest successful source entries in one transaction.
    database = _database_path()
    ingestion = ingest_fetch_results(fetched_by_index, BASE_DIR, database, predicate=lambda entry: match_entry(entry, queries))
    if not successful_sources:
        print("All RSS sources failed; keeping existing feed outputs unchanged.")
        return {
            "run_id": ingestion["run_id"], "status": ingestion["status"],
            "message": "All RSS sources failed.",
            "successful_sources": successful_sources,
            "failed_sources": failed_sources,
            "new_items": 0,
            "published": False,
            "failed": len(failed_sources),
            "errors": report["errors"],
        }

    # A successful fetch authorizes publication, including runs where no matching
    # new entries were found. Do not update this marker on a total fetch outage.
    atomic_write(JOURNAL_HASH_FILE, journal_hash)

    # SQLite contains the accepted history.  Keywords are deliberately applied
    # only while ingesting newly fetched observations above; reapplying them here
    # would make a changed RSS_KEYWORDS secret erase previously published papers
    # from the compatibility exports (and from CI's legacy bootstrap).
    all_entries = database_items(database)
    ai_report = {"failed": 0, "errors": []}
    try:
        analyze_database_items(database, all_entries, report=ai_report)
    except Exception as e:
        # AI enrichment is optional; never lose a successful fetch because of it.
        print(f"AI analysis failed; publishing without new analyses: {e}")
        ai_report["failed"] += 1
        add_error(ai_report, f"AI analysis error: {type(e).__name__}: {e}")
    taste_scored = 0
    try:
        # Score new inbox papers against an existing taste profile (never fails the fetch).
        if taste_store.load_profile(database):
            taste = _score_inbox_with_taste(database)
            taste_scored = taste.get("scored", 0)
            ai_report["failed"] += taste.get("failed", 0)
            for message in taste.get("errors") or []:
                add_error(ai_report, f"Taste score: {message}")
    except Exception as e:
        print(f"Taste scoring failed; publishing without new scores: {e}")
        ai_report["failed"] += 1
        add_error(ai_report, f"Taste score error: {type(e).__name__}: {e}")
    for message in ai_report["errors"]:
        add_error(report, message)
    all_entries = database_items(database)
    new_count = ingestion["new_observations"]
    print(f"Added {new_count} fetched matching entries.")
    generate_rss_xml(all_entries, queries)
    return {
        "run_id": ingestion["run_id"], "status": ingestion["status"],
        "successful_sources": successful_sources,
        "failed_sources": failed_sources,
        "new_items": new_count,
        "published": True,
        "ai_failed": ai_report["failed"],
        "taste_scored": taste_scored,
        "failed": len(failed_sources) + ai_report["failed"],
        "errors": report["errors"],
    }

@locked_flow("reanalyze")
def run_reanalysis_flow():
    """Reanalyse the durable store and regenerate compatibility exports from it."""
    print("Starting AI Re-analysis...")
    config = get_config()
    ai = ai_settings(config, log=True)
    if not ai["ready"]:
        print(f"AI re-analysis skipped: {ai_skip_message(ai)}.")
        return {"status": "error", "message": f"No AI backend available: {ai['reason']}",
                "failed": 0, "errors": []}
    database = _database_path()
    queries = load_config(KEYWORDS_FILE, 'RSS_KEYWORDS')
    # Reanalysis enriches every durable paper; current fetch keywords do not
    # redefine the already accepted historical collection.
    items = database_items(database)
    report = {"failed": 0, "errors": []}
    saved = analyze_database_items(database, items, config, report=report)
    items = database_items(database)
    generate_rss_xml(items, queries)
    message = f"Updated {saved} paper analyses."
    if report["failed"]:
        message += f" {report['failed']} failed."
    return {"status": "ok", "message": message, "updated": saved,
            "failed": report["failed"], "errors": report["errors"]}

def pending_summary_items(database, target_ids):
    """Resolve *target_ids* (paper_id / RSS id / legacy id) to items still lacking an AI summary."""
    return [item for item in _resolve_items(database, target_ids)
            if (item.get("abstract") or {}).get("source") not in {"gpt_summarized", "gpt_generated"}]


def existing_raw_abstract(item):
    """Raw (non-AI) abstract text already stored for *item*, if any."""
    existing = item.get("abstract") or {}
    if existing.get("raw_abstract"):
        return existing["raw_abstract"]
    if existing.get("source") in (*FETCHED_ABSTRACT_SOURCES, "user_provided"):
        return existing.get("abstract") or None
    return None


def _fetch_raw_abstracts(database, items, config=None):
    """Free lookups for *items* lacking a raw abstract.  Returns (payloads_by_id, stats).

    Never calls the AI model and never raises; nothing is saved here.
    """
    from paper_feed.ingestion import paper_dois, paper_piis, save_resolved_dois
    stats = {"fetched": 0, "failed": 0, "skipped": 0}
    ids = [item["paper_id"] for item in items]
    try:
        dois = paper_dois(database, ids)
        piis = paper_piis(database, ids)
    except Exception as e:
        print(f"Could not read stored identifiers: {e}")
        dois, piis = {}, {}
    breakers = abstract_breakers()
    jobs = []
    pii_only = []
    for item in items:
        if existing_raw_abstract(item):
            stats["skipped"] += 1
            continue
        doi = dois.get(item["paper_id"]) or entry_doi(item)
        if doi:
            jobs.append((item, doi))
        elif piis.get(item["paper_id"]):
            pii_only.append(item)
        else:
            stats["skipped"] += 1
    if pii_only:
        print(f"Resolving {len(pii_only)} ScienceDirect PIIs to DOIs via Crossref...")
        by_pii = resolve_dois_from_piis([piis[item["paper_id"]] for item in pii_only], breaker=breakers.get('crossref'))
        resolved = [by_pii.get(piis[item["paper_id"]].upper()) for item in pii_only]
        found = {item["paper_id"]: doi for item, doi in zip(pii_only, resolved) if doi}
        try:
            save_resolved_dois(database, found)
        except Exception as e:
            print(f"Could not save resolved DOIs: {e}")
        for item, doi in zip(pii_only, resolved):
            if doi:
                jobs.append((item, doi))
            else:
                stats["failed"] += 1
    payloads = {}
    if not jobs:
        return payloads, stats
    mailto = openalex_mailto(config)
    print(f"Looking up {len(jobs)} abstracts (Crossref -> OpenAlex -> Semantic Scholar)...")

    def lookup(job):
        item, doi = job
        return fetch_abstract_with_fallback({**item, "doi": doi}, mailto=mailto, breakers=breakers)

    with ThreadPoolExecutor(max_workers=min(ABSTRACT_FETCH_WORKERS, len(jobs))) as executor:
        for (item, doi), result in zip(jobs, executor.map(lookup, jobs)):
            abstract, source, raw = result if result else (None, None, None)
            if abstract and source:
                payloads[item["paper_id"]] = {"abstract": raw or abstract, "raw_abstract": raw or abstract, "source": source,
                                              "doi": doi, "fetched_at": datetime.datetime.now().isoformat()}
            else:
                stats["failed"] += 1
    return payloads, stats


def _resolve_items(database, references):
    """Map paper_ids (or RSS / legacy ids) to durable items, de-duplicated, in order."""
    item_map = {}
    for item in database_items(database):
        for key in (item["paper_id"], item.get("id"), *item.get("legacy_ids", [])):
            if key:
                item_map[str(key)] = item
    resolved, seen = [], set()
    for reference in dict.fromkeys(str(reference) for reference in references):
        item = item_map.get(reference)
        if item and item["paper_id"] not in seen:
            seen.add(item["paper_id"])
            resolved.append(item)
    return resolved


@locked_flow("fetch_abstracts")
def fetch_missing_abstracts(paper_ids=None, view='favorite'):
    """Fetch raw abstracts for free (Crossref -> OpenAlex -> Semantic Scholar); no AI, no tokens.

    Targets *paper_ids* (paper_id / RSS id / legacy id) or, when None, every
    paper in review state *view* (``'all'`` for the whole store).  Only papers
    with a DOI and no stored raw abstract are looked up; results are saved as
    ``analysis_kind='abstract'`` with ``source`` = crossref/openalex/semantic_scholar.
    Returns ``{"fetched", "failed", "skipped", "status", "message", "errors"}``.
    """
    from paper_feed.ingestion import paper_ids_in_view
    database = _database_path()
    references = list(paper_ids) if paper_ids is not None else paper_ids_in_view(database, view)
    items = _resolve_items(database, references)
    payloads, stats = _fetch_raw_abstracts(database, items, get_config())
    saved = save_db_abstracts(database, payloads) if payloads else 0
    # A protected/raced row is not an error, just nothing to do.
    stats["skipped"] += len(payloads) - saved
    stats["fetched"] = saved
    if saved:
        generate_rss_xml(database_items(database), load_config(KEYWORDS_FILE, 'RSS_KEYWORDS'))
    message = f"Fetched {saved} abstracts; {stats['failed']} not found; {stats['skipped']} skipped."
    print(message)
    return {"status": "ok", "message": message, "fetched": saved, "failed": stats["failed"],
            "skipped": stats["skipped"], "errors": []}


@locked_flow("summarize")
def summarize_specific_papers(target_ids):
    """Summarize requested durable records, then regenerate compatibility exports.

    Papers without a raw abstract but with a DOI first get a free lookup
    (Crossref -> OpenAlex -> Semantic Scholar); a found abstract is saved as
    the raw abstract and summarized (``gpt_summarized``) instead of a
    title-only guess (``gpt_generated``).  Without an API key the free
    lookups still run and AI is skipped.
    """
    print(f"Request to summarize {len(target_ids)} papers...")
    config = get_config()
    ai = ai_settings(config, log=True)
    api_key = ai["api_key"]
    database = _database_path()
    pending = pending_summary_items(database, target_ids)
    fetched_payloads, fetch_stats = _fetch_raw_abstracts(database, pending, config)
    fetched_count = save_db_abstracts(database, fetched_payloads) if fetched_payloads else 0
    queries = load_config(KEYWORDS_FILE, 'RSS_KEYWORDS')
    if not ai["ready"]:
        print(f"AI summary skipped: {ai_skip_message(ai)}.")
        if fetched_count:
            generate_rss_xml(database_items(database), queries)
            return {"status": "ok", "message": f"Fetched {fetched_count} abstracts; AI summary skipped: No AI backend available.",
                    "updated": 0, "fetched": fetched_count, "ai_skipped": True, "failed": 0, "errors": []}
        return {"status": "error", "message": f"No AI backend available: {ai['reason']}",
                "fetched": 0, "failed": 0, "errors": []}
    model = ai["model"]
    breaker = CircuitBreaker(OPENAI_BREAKER_THRESHOLD, "Codex CLI" if ai["backend"] == "codex" else "OpenAI endpoint")
    report = {"failed": 0, "errors": []}
    updates = {}
    entries = []
    for item in pending:
        existing = item.get("abstract") or {}
        fetched = fetched_payloads.get(item["paper_id"])
        raw = existing_raw_abstract(item) or (fetched or {}).get("raw_abstract")
        raw_source = existing.get("source") if existing_raw_abstract(item) else (fetched or {}).get("source")
        entries.append({"title": item.get("title") or "", "journal": item.get("journal") or "",
                        "raw": raw, "raw_source": raw_source})
    # Batched for Codex (one `codex exec` per CODEX_SUMMARY_BATCH_SIZE papers).
    outcomes = generate_summaries(entries, api_key, ai["base_url"], ai["proxy"], model=model, breaker=breaker)
    for item, entry, (summary, reason) in zip(pending, entries, outcomes):
        raw = entry["raw"]
        if summary:
            payload = {"abstract": summary, "source": "gpt_summarized" if raw else "gpt_generated",
                       "fetched_at": datetime.datetime.now().isoformat()}
            if raw:
                payload["raw_abstract"] = raw
                if entry["raw_source"]:
                    payload["raw_source"] = entry["raw_source"]
            updates[item["paper_id"]] = payload
        else:
            report["failed"] += 1
            add_error(report, f"{(item.get('title') or '')[:60]}: {reason or 'empty response'}")
    # save_abstracts never replaces a user_provided abstract edited meanwhile.
    updated_count = save_db_abstracts(database, updates)
    # Summarizing selected papers must regenerate the complete durable history,
    # regardless of later changes to the fetch keyword configuration.
    generate_rss_xml(database_items(database), queries)
    message = f"Successfully summarized {updated_count} papers."
    if fetched_count:
        message += f" Fetched {fetched_count} abstracts for free."
    if len(updates) > updated_count:
        message += f" {len(updates) - updated_count} kept user-edited abstracts."
    if report["failed"]:
        message += f" {report['failed']} failed."
    return {"status": "ok", "message": message, "updated": updated_count, "fetched": fetched_count,
            "failed": report["failed"], "errors": report["errors"]}


# --- AI taste profile (品味画像) ------------------------------------------------

TASTE_SAMPLE_LIMIT = 150
TASTE_SAMPLE_ABSTRACT_CHARS = 300
TASTE_SCORE_ABSTRACT_CHARS = 300
TASTE_PROFILE_EXTRA_SECONDS = 240
TASTE_REASON_MAX_CHARS = 120


def _taste_labels(item):
    labels = []
    if item.get("methods"):
        labels.append("method=" + ", ".join(item["methods"][:2]))
    if item.get("topics"):
        labels.append("topic=" + ", ".join(item["topics"][:3]))
    return "; ".join(labels)


def _taste_abstract(item, limit):
    raw = " ".join(str(item.get("raw_abstract") or "").split())
    return raw[:limit].rstrip() + " ..." if len(raw) > limit else raw


def _taste_sample_block(tag, item, with_abstract):
    lines = [f"[{tag}] {item.get('title') or ''}"]
    if item.get("journal"):
        lines.append(f"  Journal: {item['journal']}")
    labels = _taste_labels(item)
    if labels:
        lines.append(f"  Labels: {labels}")
    raw = _taste_abstract(item, TASTE_SAMPLE_ABSTRACT_CHARS) if with_abstract else ""
    if raw:
        lines.append(f"  Abstract: {raw}")
    return "\n".join(lines)


def taste_profile_prompt(positives, hidden):
    """Prompt asking for a semantic (not keyword) reading-taste profile in Chinese."""
    favorite = [item for item in positives if item.get("state") == "favorite"]
    archived = [item for item in positives if item.get("state") != "favorite"]
    blocks = [_taste_sample_block(f"F{n}", item, True) for n, item in enumerate(favorite, 1)]
    blocks += [_taste_sample_block(f"A{n}", item, True) for n, item in enumerate(archived, 1)]
    negative = [_taste_sample_block(f"H{n}", item, False) for n, item in enumerate(hidden, 1)]
    return f"""You are profiling one researcher's reading taste from papers they triaged in an academic RSS reader.

Samples:
- [F*] FAVORITE ({len(favorite)}): papers the researcher saved as favorites - the strongest positive signal.
- [A*] ARCHIVED ({len(archived)}): papers they read and kept for reference - a positive but weaker signal.
- [H*] HIDDEN ({len(hidden)}): papers they dismissed - the negative signal.

Infer WHY they keep some papers and dismiss others. Look past surface keywords to semantic distinctions:
- research question: what phenomenon, outcome or mechanism the paper tries to explain;
- theoretical lens: which theory or explanatory angle is used (e.g. psychological process vs. economic incentives);
- context: domain, population, setting or level of analysis (consumers, firms, platforms, policy ...);
- method: experiments, archival/empirical data, modelling, qualitative, review, etc.
Many liked and hidden papers share a topic word. Compare such pairs and state the deciding difference as a boundary judgment, e.g. "同样研究 AI，偏好消费者对 AI 的心理反应，不看企业 AI 采纳的宏观影响". Never cite sample labels such as F4 or H36 in the output (the reader cannot see them); name the kind of paper instead. Every judgment must be supported by the samples; do not invent preferences the samples do not show. If the samples are thin on some aspect, say so in the summary instead of guessing.

Write everything in Simplified Chinese (keep established English theory or method names if clearer). Be specific and concrete; avoid generic phrases such as "关注高质量研究".

Output valid JSON with exactly these keys:
{{"summary": "一段 150-300 字的中文总体描述：核心兴趣、偏好的问题类型与视角、明显回避的方向",
  "likes": ["偏好的研究问题/理论视角，每条一句，4-10 条"],
  "dislikes": ["不感兴趣的方向，每条一句，3-8 条"],
  "boundaries": ["边界判断：同样是X，要Y不要Z（对比相似主题的收藏与隐藏论文），3-8 条"],
  "methods": ["偏好的研究方法/情境/样本，每条一句，2-6 条"]}}
No HTML tags or angle brackets. At most {taste_store.TASTE_MAX_ITEMS} items per list.

Positive samples:

""" + "\n".join(blocks) + "\n\nHidden samples:\n\n" + "\n".join(negative)


def _taste_result(status, message, profile=None, failed=0, errors=None):
    return {"status": status, "message": message, "profile": profile, "failed": failed, "errors": list(errors or [])}


@locked_flow("taste_profile")
def generate_taste_profile(config=None):
    """Infer a new taste profile from favorite + archived vs hidden papers (one AI call).

    Uses the TASTE_SAMPLE_LIMIT most recently reviewed positives and hidden
    papers.  Returns ``{"status": "ok"|"error"|"skipped", "message", "profile",
    "failed", "errors"}``; skipped when AI is not ready or samples are too few.
    """
    config = get_config() if config is None else config
    database = _database_path()
    counts = taste_store.sample_counts(database)
    positives = counts["favorite"] + counts["archived"]
    if not taste_store.has_enough_samples(counts):
        message = (f"Not enough samples for a taste profile: {positives} favorite/archived and {counts['hidden']} hidden "
                   f"(need >= {taste_store.TASTE_MIN_POSITIVES} positives and >= {taste_store.TASTE_MIN_SAMPLES} in total). "
                   f"样本不足：需要至少 {taste_store.TASTE_MIN_POSITIVES} 篇收藏/归档，且总样本至少 {taste_store.TASTE_MIN_SAMPLES} 篇。")
        print(message)
        return _taste_result("skipped", message)
    ai = ai_settings(config, log=True)
    if not ai["ready"]:
        print(f"Taste profile skipped: {ai_skip_message(ai)}.")
        return _taste_result("skipped", f"No AI backend available: {ai['reason']}")
    samples = taste_store.collect_samples(database, TASTE_SAMPLE_LIMIT)
    api_key = ai["api_key"]
    breaker = CircuitBreaker(OPENAI_BREAKER_THRESHOLD, "Codex CLI" if ai["backend"] == "codex" else "OpenAI endpoint")
    print(f"Generating taste profile from {len(samples['positive'])} positive and {len(samples['hidden'])} hidden "
          f"samples with {ai_backend_label(ai)}...")
    try:
        client = make_openai_client(api_key, ai["base_url"], ai["proxy"])
        if is_codex_backend(api_key):
            # Up to 300 samples in one prompt: allow a longer `codex exec`.
            base = max(api_key.timeout or CODEX_TIMEOUT_SECONDS, CODEX_TIMEOUT_SECONDS)
            client = client.with_options(timeout=base + TASTE_PROFILE_EXTRA_SECONDS)
        response = chat_completion_with_retry(
            client,
            breaker=breaker,
            model=ai["model"] or DEFAULT_OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a careful academic research assistant and a JSON-only API. "
                                              "You analyse a researcher's reading choices and describe their taste "
                                              "in Chinese, grounded only in the evidence given."},
                {"role": "user", "content": taste_profile_prompt(samples["positive"], samples["hidden"])},
            ],
            max_tokens=2500,
            temperature=0.3,
            response_format={"type": "json_object"},
        )
        data = parse_model_json(response.choices[0].message.content or "")
        if isinstance(data, dict) and isinstance(data.get("profile"), dict):
            data = data["profile"]
        profile = taste_store.save_profile(database, data, "ai", model=ai["model"] or "", sample_counts=counts)
    except Exception as e:
        print(f"Taste profile error: {e}")
        reason = f"{type(e).__name__}: {e}"[:300]
        return _taste_result("error", f"Taste profile generation failed: {reason}", failed=1, errors=[reason])
    message = (f"Generated taste profile {profile['version']} from {len(samples['positive'])} positive and "
               f"{len(samples['hidden'])} hidden samples (most recent). 已生成品味画像。")
    print(message)
    return _taste_result("ok", message, profile=profile)


def stale_taste_items(items, profile):
    """Inbox items whose taste score is missing or was computed against another profile version.

    Accepts ``taste.inbox_items`` rows (``taste`` payload) and service records
    (``taste_score`` / ``taste_profile_version``); items with a non-inbox
    ``state`` are ignored.
    """
    version = (profile or {}).get("version")
    if not version:
        return []
    stale = []
    for item in items:
        if item.get("state") not in (None, "inbox"):
            continue
        payload = item.get("taste") if isinstance(item.get("taste"), dict) else {
            "score": item.get("taste_score"), "profile_version": item.get("taste_profile_version")}
        if payload.get("score") is None or payload.get("profile_version") != version:
            stale.append(item)
    return stale


def pending_taste_items(database=None):
    """Stale inbox items for the current profile ([] when there is no profile)."""
    database = database or _database_path()
    profile = taste_store.load_profile(database)
    if not profile:
        return []
    return stale_taste_items(taste_store.inbox_items(database), profile)


def taste_score_prompt(profile, entries):
    blocks = []
    for position, entry in enumerate(entries, 1):
        lines = [f"[{position}] Title: {entry.get('title') or ''}"]
        if entry.get("journal"):
            lines.append(f"Journal: {entry['journal']}")
        labels = _taste_labels(entry)
        if labels:
            lines.append(f"Labels: {labels}")
        raw = _taste_abstract(entry, TASTE_SCORE_ABSTRACT_CHARS)
        if raw:
            lines.append(f"Abstract: {raw}")
        blocks.append("\n".join(lines))
    return f"""Rate how well each of the {len(entries)} papers below matches one researcher's reading taste.

Taste profile (Chinese):
{taste_store.profile_text(profile)}

Judge the research question, theoretical lens, context and method, not shared keywords. Apply the boundary judgments: a paper on a liked topic but from a disliked angle scores low.
Score 0-100: 90-100 core interest; 75-89 strong match; 50-74 partial match; 25-49 weak; 0-24 matches a dislike or is unrelated.

Rules:
- Treat each paper independently; never mix information between papers.
- "reason": one short Chinese sentence (at most 40 characters) naming the preference or boundary that decided the score. No HTML tags or angle brackets.
- Output valid JSON: {{"results": [{{"index": 1, "score": 82, "reason": "..."}}]}} with exactly one result per paper; "index" is the paper's number in brackets, copied exactly (1-based); "score" is an integer.

Papers:

""" + "\n\n".join(blocks)


def batch_score_with_taste(entries, profile, api_key, base_url=None, proxy=None, model=None, breaker=None):
    """Score *entries* against *profile* in one AI call.

    Returns ``[(result | None, error | None)]`` in input order with
    ``result = {"score": int 0-100, "reason": str}``; aligned by the echoed
    1-based ``index`` (``align_batch_results``).  Never raises.
    """
    if not entries:
        return []
    if not api_key:
        return [(None, "no AI backend") for _ in entries]
    try:
        client = make_openai_client(api_key, base_url, proxy)
        response = chat_completion_with_retry(
            client,
            breaker=breaker,
            model=model or DEFAULT_OPENAI_MODEL,
            messages=[
                {"role": "system", "content": "You are a JSON-only API that matches academic papers to a "
                                              "researcher's taste profile and explains each score in Chinese."},
                {"role": "user", "content": taste_score_prompt(profile, entries)},
            ],
            max_tokens=80 * len(entries) + 200,
            temperature=0.2,
            response_format={"type": "json_object"},
        )
        data = parse_model_json(response.choices[0].message.content or "")
    except Exception as e:
        print(f"Taste scoring error: {e}")
        reason = f"{type(e).__name__}: {e}"
        return [(None, reason) for _ in entries]
    result_list = data.get("results", []) if isinstance(data, dict) else []
    if not isinstance(result_list, list):
        result_list = []
    outcomes = [(None, "not returned by the model; left pending") for _ in entries]
    for position, result in align_batch_results(list(range(len(entries))), result_list):
        score = taste_store.clamp_score(result.get("score")) if isinstance(result, dict) else None
        if score is None:
            outcomes[position] = (None, "invalid score in batch response")
            continue
        reason = _ANGLE_TAG_RE.sub("", str(result.get("reason") or "")).replace("<", "").replace(">", "")
        outcomes[position] = ({"score": score, "reason": " ".join(reason.split())[:TASTE_REASON_MAX_CHARS]}, None)
    return outcomes


@locked_flow("taste_score")
def score_inbox_with_taste(config=None, rescore=False):
    """Score stale (or, with *rescore*, all) inbox papers against the current taste profile.

    Returns ``{"status": "ok"|"partial_failed"|"skipped"|"error", "message",
    "scored", "failed", "skipped", "errors"}``.
    """
    return _score_inbox_with_taste(_database_path(), config, rescore)


def _score_inbox_with_taste(database, config=None, rescore=False):
    """Unlocked implementation shared by ``score_inbox_with_taste`` and ``run_rss_flow``."""
    result = {"status": "ok", "message": "", "scored": 0, "failed": 0, "skipped": 0, "errors": []}
    profile = taste_store.load_profile(database)
    if not profile:
        result.update(status="skipped", message="No taste profile yet; generate one first. 尚无品味画像，请先生成。")
        return result
    items = taste_store.inbox_items(database)
    targets = list(items) if rescore else stale_taste_items(items, profile)
    result["skipped"] = len(items) - len(targets)
    if not targets:
        result["message"] = "Every inbox paper already has a current taste score. 待筛选论文均已打分。"
        return result
    config = get_config() if config is None else config
    ai = ai_settings(config, log=True)
    if not ai["ready"]:
        print(f"Taste scoring skipped for {len(targets)} papers: {ai_skip_message(ai)}.")
        result.update(status="skipped", message=f"No AI backend available: {ai['reason']}",
                      skipped=result["skipped"] + len(targets))
        return result
    codex = ai["backend"] == "codex"
    # Each `codex exec` costs a large fixed prompt overhead: fewer, larger calls.
    size = max(1, CODEX_ANALYSIS_CHUNK_SIZE if codex else OPENAI_ANALYSIS_CHUNK_SIZE)
    chunks = [targets[start:start + size] for start in range(0, len(targets), size)]
    breaker = CircuitBreaker(OPENAI_BREAKER_THRESHOLD, "Codex CLI" if codex else "OpenAI endpoint")

    def run(chunk):
        return chunk, batch_score_with_taste(chunk, profile, ai["api_key"], ai["base_url"], ai["proxy"],
                                             model=ai["model"], breaker=breaker)

    workers = min(CODEX_ANALYSIS_WORKERS if codex else AI_ANALYSIS_WORKERS, len(chunks)) or 1
    print(f"Scoring {len(targets)} inbox papers against taste profile {profile['version']} in {len(chunks)} "
          f"batch(es) with {ai_backend_label(ai)}...")
    scores = {}
    with ThreadPoolExecutor(max_workers=workers) as executor:
        for chunk, outcomes in executor.map(run, chunks):
            for item, (scored, reason) in zip(chunk, outcomes):
                if scored:
                    scores[item["paper_id"]] = scored
                else:
                    result["failed"] += 1
                    add_error(result, f"{(item.get('title') or '')[:60]}: {reason or 'empty response'}")
    result["scored"] = taste_store.save_scores(database, scores, profile["version"])
    message = f"Scored {result['scored']} inbox papers. 已为 {result['scored']} 篇待筛选论文打分。"
    if result["failed"]:
        message += f" {result['failed']} failed."
        result["status"] = "partial_failed" if result["scored"] else "error"
    result["message"] = message
    print(message)
    return result


def main(argv=None):
    """Compatibility entry point: `python get_RSS.py` == `python -m paper_feed refresh`.

    Exit codes: 0 published, 1 all sources failed, 2 config error.
    """
    from paper_feed.cli import main as cli_main
    return cli_main(["refresh", *(sys.argv[1:] if argv is None else argv)])


if __name__ == '__main__':
    # Share this module object with the CLI instead of importing the file twice.
    sys.modules.setdefault("get_RSS", sys.modules[__name__])
    sys.exit(main())
