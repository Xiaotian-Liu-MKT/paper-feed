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
CONFIG_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_PROXY", "OPENAI_MODEL")
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"
CONFIG_DEFAULTS = {"OPENAI_MODEL": DEFAULT_OPENAI_MODEL}
OPENAI_RETRY_ATTEMPTS = 3
OPENAI_RETRY_BASE_DELAY = 2.0
# Per-request ceiling.  Timeouts are not retried and a shared CircuitBreaker
# stops a job after repeated timeouts, so a hung endpoint costs ~1-2 timeouts.
OPENAI_TIMEOUT_SECONDS = 60
OPENAI_BREAKER_THRESHOLD = 2
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
    request is bounded by ``OPENAI_TIMEOUT_SECONDS``.
    """
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
    try:
        import openai
    except ImportError:
        return False
    connection = getattr(openai, "APIConnectionError", None)
    return bool(connection and isinstance(error, connection))


def is_retryable_openai_error(error):
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


def _abstract_api_get(url, source, params=None, breaker=None):
    """GET JSON from a free metadata API; returns dict or None and never raises."""
    if breaker is not None and breaker.is_open:
        return None
    try:
        response = requests.get(url, params=params, timeout=ABSTRACT_HTTP_TIMEOUT,
                                headers={"User-Agent": ABSTRACT_USER_AGENT, "Accept": "application/json"})
    except Exception as e:
        print(f"{source} request failed: {type(e).__name__}: {e}")
        if breaker is not None:
            breaker.record_failure()
        return None
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
            lookups = [
                ('crossref', lambda: get_abstract_from_crossref(doi, breaker=breakers.get('crossref'))),
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

    if proxy:
        print(f"Using proxy: {proxy}")
    try:
        client = make_openai_client(api_key, base_url, proxy)
    except Exception as e:
        # A bad proxy URL or missing dependency must not abort the RSS refresh.
        print(f"Could not create OpenAI client; skipping AI analysis: {e}")
        if report is not None:
            report["failed"] += len(titles)
        add_error(report, f"OpenAI client error: {type(e).__name__}: {e}")
        return {}
    model = model or DEFAULT_OPENAI_MODEL

    analysis_results = {}
    chunk_size = 10
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

    breaker = CircuitBreaker(OPENAI_BREAKER_THRESHOLD, "OpenAI endpoint")

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
            content = response.choices[0].message.content
            data = json.loads(content)
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

    worker_count = min(AI_ANALYSIS_WORKERS, len(chunks))
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
    api_key = config.get("OPENAI_API_KEY")
    base_url = config.get("OPENAI_BASE_URL")
    proxy = config.get("OPENAI_PROXY")

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
        print("AI analysis skipped: no OPENAI_API_KEY configured (set it in config.json or the environment).")

    # 执行分析
    if titles_to_analyze:
        print(f"Analyzing {len(titles_to_analyze)} papers (Translation + Classification)...")
        new_results = batch_analyze_papers(titles_to_analyze, api_key, base_url, proxy, model=config_model(config))
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
    config = config or get_config()
    api_key = config.get("OPENAI_API_KEY")
    stale = stale_analysis_items(items)
    if not stale:
        return 0
    if not api_key:
        print(f"AI analysis skipped for {len(stale)} papers: no OPENAI_API_KEY configured "
              "(set it in config.json or the environment).")
        return 0
    titles = list(dict.fromkeys(item["title"] for item in stale))
    results = batch_analyze_papers(titles, api_key, config.get("OPENAI_BASE_URL"), config.get("OPENAI_PROXY"),
                                   model=config_model(config), report=report) or {}
    durable = {item["paper_id"]: results[item["title"]] for item in stale if item["title"] in results}
    return save_db_translations(database, durable)

def _database_path():
    return ensure_database(BASE_DIR, os.environ.get("PAPER_FEED_DB") or None)

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
        "failed": len(failed_sources) + ai_report["failed"],
        "errors": report["errors"],
    }

def run_reanalysis_flow():
    """Reanalyse the durable store and regenerate compatibility exports from it."""
    print("Starting AI Re-analysis...")
    config = get_config()
    if not config.get("OPENAI_API_KEY"):
        print("AI re-analysis skipped: no OPENAI_API_KEY configured.")
        return {"status": "error", "message": "No API Key configured.", "failed": 0, "errors": []}
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
    from paper_feed.ingestion import paper_dois
    stats = {"fetched": 0, "failed": 0, "skipped": 0}
    try:
        dois = paper_dois(database, [item["paper_id"] for item in items])
    except Exception as e:
        print(f"Could not read stored DOIs: {e}")
        dois = {}
    jobs = []
    for item in items:
        if existing_raw_abstract(item):
            stats["skipped"] += 1
            continue
        doi = dois.get(item["paper_id"]) or entry_doi(item)
        if not doi:
            stats["skipped"] += 1
            continue
        jobs.append((item, doi))
    payloads = {}
    if not jobs:
        return payloads, stats
    mailto = openalex_mailto(config)
    breakers = abstract_breakers()
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
    api_key = config.get("OPENAI_API_KEY")
    database = _database_path()
    pending = pending_summary_items(database, target_ids)
    fetched_payloads, fetch_stats = _fetch_raw_abstracts(database, pending, config)
    fetched_count = save_db_abstracts(database, fetched_payloads) if fetched_payloads else 0
    queries = load_config(KEYWORDS_FILE, 'RSS_KEYWORDS')
    if not api_key:
        print("AI summary skipped: no OPENAI_API_KEY configured.")
        if fetched_count:
            generate_rss_xml(database_items(database), queries)
            return {"status": "ok", "message": f"Fetched {fetched_count} abstracts; AI summary skipped: No API Key configured.",
                    "updated": 0, "fetched": fetched_count, "ai_skipped": True, "failed": 0, "errors": []}
        return {"status": "error", "message": "No API Key configured.", "fetched": 0, "failed": 0, "errors": []}
    model = config_model(config)
    breaker = CircuitBreaker(OPENAI_BREAKER_THRESHOLD, "OpenAI endpoint")
    report = {"failed": 0, "errors": []}
    updates = {}
    for item in pending:
        existing = item.get("abstract") or {}
        fetched = fetched_payloads.get(item["paper_id"])
        raw = existing_raw_abstract(item) or (fetched or {}).get("raw_abstract")
        raw_source = existing.get("source") if existing_raw_abstract(item) else (fetched or {}).get("source")
        call_errors = []
        if raw:
            summary = summarize_abstract_with_gpt(raw, item["title"], api_key, config.get("OPENAI_BASE_URL"), config.get("OPENAI_PROXY"),
                                                  model=model, errors=call_errors, breaker=breaker)
            source = "gpt_summarized"
        else:
            summary = generate_abstract_with_gpt(item["title"], item["journal"], api_key, config.get("OPENAI_BASE_URL"), config.get("OPENAI_PROXY"),
                                                 model=model, errors=call_errors, breaker=breaker)
            source = "gpt_generated"
        if summary:
            payload = {"abstract": summary, "source": source, "fetched_at": datetime.datetime.now().isoformat()}
            if raw:
                payload["raw_abstract"] = raw
                if raw_source:
                    payload["raw_source"] = raw_source
            updates[item["paper_id"]] = payload
        else:
            report["failed"] += 1
            reason = call_errors[0] if call_errors else "empty response"
            add_error(report, f"{(item.get('title') or '')[:60]}: {reason}")
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
