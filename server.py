import http.server
import socketserver
import json
import os
import sys
import datetime
import html
import re
import tempfile
import threading
import queue
import uuid
import errno
from functools import partial
from urllib.parse import parse_qs, urlparse
from paper_feed.service import PaperFeedService, PaperNotFound, PaperReferenceError

# 导入 RSS 抓取逻辑
# 确保 get_RSS.py 在同一目录下
try:
    from get_RSS import (run_rss_flow, get_config, summarize_specific_papers, configure_stdio,
                         fallback_label, preview_keywords, DEFAULT_OPENAI_MODEL)
except ImportError as import_error:
    missing = getattr(import_error, "name", None) or str(import_error)
    print(f"Error: could not import get_RSS.py because a dependency is missing: {missing}")
    print("Install the project requirements into the virtual environment, e.g.:")
    print("  Windows: .venv\\Scripts\\python.exe -m pip install -r requirements.txt")
    print("  macOS/Linux: .venv/bin/python -m pip install -r requirements.txt")
    sys.exit(1)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_PORT = 8000
PORT = DEFAULT_PORT
WEB_DIR = os.path.join(BASE_DIR, "web")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")
INTERACTIONS_FILE = os.path.join(WEB_DIR, "interactions.json")
FEED_FILE = os.path.join(WEB_DIR, "feed.json")
REPORT_FILE = os.path.join(WEB_DIR, "preference_report.json")
CATEGORIES_FILE = os.path.join(WEB_DIR, "categories.json")
USER_CORRECTIONS_FILE = os.path.join(WEB_DIR, "user_corrections.json")
JOURNALS_FILE = os.path.join(BASE_DIR, "journals.dat")
KEYWORDS_FILE = os.path.join(BASE_DIR, "keywords.dat")
JOURNALS_META_FILE = os.path.join(BASE_DIR, "journals_meta.json")
RSS_LIST_FILE = os.path.join(BASE_DIR, "RSS list.md")
FILE_LOCK = threading.RLock()
MAX_LISTED_JOBS = 20
CONFIG_SAVE_KEYS = ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_PROXY", "OPENAI_MODEL",
                    "AI_BACKEND", "CODEX_MODEL", "CODEX_REASONING_EFFORT", "CODEX_PATH")
MAX_REQUEST_BODY_BYTES = 2 * 1024 * 1024
MAX_ABSTRACT_CHARS = 50_000
TEST_CONNECTION_TIMEOUT = 20
# One `codex exec` start-up (login check, ~20k-token system prompt) takes seconds.
CODEX_TEST_CONNECTION_TIMEOUT = 120
# Abstract sources that are real (non-AI) text and may be exported as RIS AB.
RIS_ABSTRACT_SOURCES = {"user_provided", "crossref", "semantic_scholar", "openalex"}


def paper_service():
    """A request-scoped service; connections are never shared by HTTP threads.

    The local server never auto-imports the tracked legacy exports into a new
    database; users run `python -m paper_feed import-legacy` explicitly.
    """
    return PaperFeedService(BASE_DIR, os.environ.get("PAPER_FEED_DB"), import_legacy=False)


def atomic_write_text(path, content, encoding="utf-8"):
    """Replace a file only after its complete contents reach disk."""
    directory = os.path.dirname(os.path.abspath(path)) or "."
    os.makedirs(directory, exist_ok=True)
    fd, temporary_path = tempfile.mkstemp(prefix=".paper-feed-", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    except Exception:
        try:
            os.unlink(temporary_path)
        except OSError:
            pass
        raise


def atomic_write_json(path, payload, ensure_ascii=False):
    atomic_write_text(path, json.dumps(payload, ensure_ascii=ensure_ascii, indent=2))


class JobRunner:
    """One local worker prevents concurrent refresh/reanalysis writes."""
    def __init__(self):
        self._jobs = {}
        self._order = []
        self._lock = threading.RLock()
        self._queue = queue.Queue()
        self._worker = threading.Thread(target=self._run, daemon=True, name="paper-feed-jobs")
        self._worker.start()

    def enqueue(self, kind, action):
        with self._lock:
            for job in self._jobs.values():
                if job["kind"] == kind and job["status"] in {"queued", "running"}:
                    return dict(job), True
            job_id = uuid.uuid4().hex
            job = {"id": job_id, "kind": kind, "status": "queued", "stage": "queued",
                   "progress": 0, "message": "任务已排队", "created_at": datetime.datetime.now().isoformat(),
                   "started_at": None, "finished_at": None, "result": None}
            self._jobs[job_id] = job
            self._order.append(job_id)
            self._prune()
            self._queue.put((job_id, action))
            return dict(job), False

    def _prune(self, keep=100):
        """Forget the oldest finished jobs so a long-running server stays small."""
        while len(self._order) > keep:
            for index, job_id in enumerate(self._order):
                if self._jobs[job_id]["status"] not in {"queued", "running"}:
                    del self._jobs[job_id]
                    del self._order[index]
                    break
            else:
                return

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list(self, limit=MAX_LISTED_JOBS):
        """Most recent first, so a reloaded page can find a running job."""
        with self._lock:
            return [dict(self._jobs[job_id]) for job_id in reversed(self._order[-limit:])]

    @staticmethod
    def classify(result):
        """Map an action result to (status, message)."""
        if not isinstance(result, dict):
            return "succeeded", "任务完成"
        failed = result.get("failed") or 0
        try:
            failed = int(failed)
        except (TypeError, ValueError):
            failed = 0
        if result.get("status") == "error":
            return "failed", result.get("message") or "任务失败"
        if result.get("published") is False and not result.get("successful_sources"):
            return "failed", result.get("message") or "任务失败：所有来源均失败"
        if "updated" in result and failed and not result.get("updated"):
            return "failed", result.get("message") or f"任务失败：{failed} 项失败"
        if result.get("failed_sources"):
            return "partial_failed", "任务完成，部分来源失败"
        if failed > 0:
            return "partial_failed", f"任务完成，{failed} 项失败"
        return "succeeded", "任务完成"

    def _run(self):
        while True:
            job_id, action = self._queue.get()
            with self._lock:
                job = self._jobs[job_id]
                job.update(status="running", stage="processing", progress=10,
                           message="任务正在执行", started_at=datetime.datetime.now().isoformat())
            try:
                result = action() or {}
                status, message = self.classify(result)
                with self._lock:
                    job.update(status=status, stage="completed", progress=100, message=message,
                               finished_at=datetime.datetime.now().isoformat(), result=result)
            except Exception as error:
                with self._lock:
                    job.update(status="failed", stage="failed", progress=100,
                               message=str(error), finished_at=datetime.datetime.now().isoformat(), result={})
            finally:
                self._queue.task_done()


JOB_RUNNER = JobRunner()


def run_fetch_job():
    return run_rss_flow()


def run_reanalysis_job():
    from get_RSS import run_reanalysis_flow
    return run_reanalysis_flow()


def run_summarize_job():
    favorites = paper_service().favorite_legacy_ids()
    legacy_ids = [legacy_id for _, legacy_id in favorites if legacy_id]
    if not legacy_ids:
        return {"message": "No favorites to summarize.", "summarized": 0}
    # The legacy summarizer needs RSS ids.  Selection is SQLite-backed, so a link
    # that differs from RSS id still selects the correct paper.
    # The summarizer writes its result directly to SQLite.  Do not re-import the
    # legacy cache here: it may contain an older version of the same abstract.
    return summarize_specific_papers(legacy_ids)


def apply_interaction_change(request_data):
    service = paper_service()
    paper_id = service.resolve_reference(request_data)
    service.review(paper_id, request_data.get("action"))
    return service.interactions()

TITLE_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has", "have",
    "in", "into", "is", "it", "its", "of", "on", "or", "over", "that", "the", "their",
    "this", "to", "was", "were", "with", "within", "without", "how", "what", "when",
    "where", "which", "who", "whom", "why", "does", "do", "did", "done", "can", "could",
    "may", "might", "must", "should", "would", "via", "toward", "towards", "between",
    "across", "among", "through", "during", "under", "above", "below", "than", "then",
    "these", "those", "we", "our", "you", "your", "they", "them", "he", "she", "his",
    "her", "i", "me", "my", "study", "studies", "research", "evidence", "effect",
    "effects", "analysis", "approach", "model", "models", "role", "impact", "impacts",
}

META_LABEL_REGEX = re.compile(r"(Publication date|Source|Authors?\(s\)?)\s*:\s*", re.IGNORECASE)

def tokenize_title(title):
    if not title or not isinstance(title, str):
        return []
    cleaned = []
    for ch in title.lower():
        if ch.isalnum():
            cleaned.append(ch)
        else:
            cleaned.append(" ")
    tokens = [t for t in "".join(cleaned).split() if len(t) >= 3]
    result = []
    for token in tokens:
        if token in TITLE_STOPWORDS:
            continue
        if token.isdigit():
            continue
        result.append(token)
    return result

def clean_journal_name(name):
    if not name or not isinstance(name, str):
        return ""
    clean = name.strip()
    if clean.lower() == "latest results":
        return "Journal of the Academy of Marketing Science"
    prefix_patterns = [
        r"^sciencedirect(?:\s+publication)?\s*[:\-]\s*",
        r"^wiley\s*[:\-]\s*",
        r"^sage publications inc\s*[:\-]\s*",
        r"^sage publications ltd\s*[:\-]\s*",
        r"^tandf\s*[:\-]\s*",
        r"^iorms\s*[:\-]\s*",
        r"^academy of management\s*[:\-]\s*",
        r"^the university of chicago press\s*[:\-]\s*",
    ]
    suffix_patterns = [
        r"\s*[:\-]?\s*table of contents\s*$",
        r"\s*[:\-]?\s*advance access\s*$",
        r"\s*[:\-]?\s*latest results\s*$",
        r"\s*[:\-]?\s*vol(?:ume)?\s*\d+\s*,?\s*iss(?:ue)?\.?\s*\d+\s*$",
        r"\s*[:\-]?\s*vol(?:ume)?\s*\d+\s*$",
        r"\s*[:\-]?\s*iss(?:ue)?\.?\s*\d+\s*$",
    ]
    changed = True
    while changed:
        changed = False
        for pattern in prefix_patterns:
            next_val = re.sub(pattern, "", clean, flags=re.IGNORECASE)
            if next_val != clean:
                clean = next_val
                changed = True
        for pattern in suffix_patterns:
            next_val = re.sub(pattern, "", clean, flags=re.IGNORECASE)
            if next_val != clean:
                clean = next_val
                changed = True
    clean = re.sub(r"\s*\[.*?\]\s*$", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean


def _ris_clean(value):
    """Return a single safe RIS field value without allowing tag injection."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _ris_authors(summary):
    """Extract the feed's conventional Author(s) value into RIS author names."""
    # Feeds use all four variants below.  Stop at every recognized metadata
    # label so an author field cannot absorb Source or Publication date values.
    author_label = r"(?:Author|Authors|Author\(s\)|Authors\(s\))"
    field_label = rf"(?:Publication\s+date|Source|{author_label})"
    match = re.search(rf"(?:^|\s){author_label}\s*:\s*(.*?)(?=\s+{field_label}\s*:|$)",
                      str(summary or ""), re.IGNORECASE)
    raw = match.group(1).strip(" \t\r\n;,.") if match else ""
    if not raw:
        return []
    normalized = re.sub(r"\s+(?:and|&)\s+", ";", raw, flags=re.IGNORECASE)
    parts = (normalized.split(";") if ";" in normalized else normalized.split(","))
    authors = []
    for part in parts:
        name = _ris_clean(part)
        if not name:
            continue
        tokens = name.split()
        authors.append(f"{tokens[-1]}, {' '.join(tokens[:-1])}" if len(tokens) > 1 else name)
    return authors


def _ris_date(value):
    """Return the RIS PY value; tolerate incomplete or malformed feed dates."""
    match = re.match(r"\s*(\d{4})", str(value or ""))
    return match.group(1) if match else ""


def build_favorite_ris_entry(item):
    """Build one canonical RIS record from a SQLite-projected paper."""
    title = _ris_clean(item.get("title")) or "Untitled"
    journal = _ris_clean(clean_journal_name(item.get("journal", "")))
    url = _ris_clean(item.get("link"))
    lines = ["TY  - JOUR", f"TI  - {title}"]
    if journal:
        lines.append(f"JO  - {journal}")
    for author in _ris_authors(item.get("summary", "")):
        lines.append(f"AU  - {author}")
    year = _ris_date(item.get("pub_date"))
    if year:
        lines.append(f"PY  - {year}")
    if url:
        lines.append(f"UR  - {url}")
    doi = _ris_clean(item.get("doi"))
    if doi:
        lines.append(f"DO  - {doi}")
    raw_abstract = _ris_clean(item.get("raw_abstract"))
    if raw_abstract and item.get("abstract_source") in RIS_ABSTRACT_SOURCES:
        lines.append(f"AB  - {raw_abstract}")
    # This stable note makes the export traceable even if legacy source metadata
    # is absent.  It deliberately uses the durable SQLite identity, not feed ids.
    lines.append(f"N1  - Paper Feed ID: {_ris_clean(item.get('paper_id'))}")
    lines.append("ER  - ")
    return "\r\n".join(lines)


def build_favorites_ris(service=None):
    """Export the current favorite view as RIS without mutating the database."""
    service = service or paper_service()
    items = service.list_papers("favorite")
    entries = [build_favorite_ris_entry(item) for item in items]
    return {
        "count": len(entries),
        "ris": ("\r\n\r\n".join(entries) + "\r\n") if entries else "",
    }

def extract_meta_value(text, label):
    if not text:
        return ""
    pattern = re.compile(rf"{label}\s*:\s*", re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    rest = text[start:]
    stop = META_LABEL_REGEX.search(rest)
    if stop:
        rest = rest[:stop.start()]
    return rest.strip(" \t\r\n;,-")

def parse_summary_source(summary):
    if not summary or not isinstance(summary, str):
        return ""
    cleaned = html.unescape(summary)
    cleaned = re.sub(r"<[^>]+>", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return extract_meta_value(cleaned, "Source")

def generate_data_quality_warnings(favorites_count, hidden_count):
    """生成数据质量警告"""
    warnings = []
    recommendations = []

    balance_ratio = favorites_count / max(hidden_count, 1)

    if balance_ratio < 0.1:
        severity = "severe"
        warnings.append({
            "type": "imbalance",
            "message": f"收藏/归档样本过少（{favorites_count}），可能导致偏好推断不准确。建议至少收藏100篇论文。",
            "severity": "high"
        })
    elif balance_ratio < 0.3:
        severity = "moderate"
        warnings.append({
            "type": "imbalance",
            "message": f"样本不够平衡（收藏/归档{favorites_count} vs 隐藏{hidden_count}），偏好分析可能偏向隐藏模式。",
            "severity": "medium"
        })
    else:
        severity = "acceptable"

    if favorites_count < 50:
        recommendations.append("继续收藏或归档更多论文以提高推断准确性")

    recommendations.append("对lift值<2或样本量<5的词汇持保留态度")

    return {
        "sample_balance_ratio": round(balance_ratio, 3),
        "imbalance_severity": severity,
        "warnings": warnings,
        "recommendations": recommendations
    }

def infer_research_area(keywords):
    """根据关键词推断研究领域"""
    keywords_lower = [k.lower() for k in keywords]
    area_map = {
        ('consumer', 'behavior', 'choice', 'decision'): '消费者行为',
        ('sustainability', 'green', 'csr', 'ethical'): '可持续发展与企业社会责任',
        ('ai', 'algorithmic', 'genai', 'robot', 'automation'): '人工智能与营销',
        ('digital', 'platform', 'online', 'social'): '数字营销',
        ('food', 'health', 'wellbeing', 'nutrition'): '健康与食品营销',
        ('tourism', 'hotel', 'hospitality', 'travel'): '旅游与酒店管理',
        ('brand', 'advertising', 'market'): '品牌与市场营销'
    }

    for keys, area in area_map.items():
        if any(k in keywords_lower for k in keys):
            return area
    return '营销学'

def generate_insights_summary(report):
    """基于报告数据自动生成总结性洞察"""
    insights = []

    # 1. 核心研究兴趣
    terms = report.get('title_terms', {})
    top_fav = terms.get('top_favorites', [])[:5]
    if top_fav:
        keywords = [t['term'] for t in top_fav]
        area = infer_research_area(keywords)
        insights.append({
            'category': 'core_interest',
            'title': '您的核心研究兴趣',
            'content': f"您最关注 {', '.join(keywords[:3])} 等主题，这反映了您在{area}领域的研究偏好。"
        })

    # 2. 方法偏好
    methods = report.get('method_topic', {}).get('methods', {})
    preferred_methods = methods.get('preferred', [])[:3]
    if preferred_methods and preferred_methods[0].get('lift', 0) > 1.5:
        method_name = preferred_methods[0]['label']
        lift_val = preferred_methods[0]['lift']
        insights.append({
            'category': 'method_preference',
            'title': '研究方法偏好',
            'content': f"您更偏好 {method_name} 类研究（lift={lift_val:.2f}），建议关注该方法的最新进展。"
        })

    # 3. 主题偏好
    topics = report.get('method_topic', {}).get('topics', {})
    preferred_topics = topics.get('preferred', [])[:3]
    if preferred_topics and preferred_topics[0].get('lift', 0) > 1.5:
        topic_name = preferred_topics[0]['label']
        lift_val = preferred_topics[0]['lift']
        insights.append({
            'category': 'topic_preference',
            'title': '研究主题偏好',
            'content': f"您对 {topic_name} 类论文更感兴趣（lift={lift_val:.2f}）。"
        })

    # 4. 避免的话题
    avoided = terms.get('avoided', [])[:3]
    if avoided and avoided[0].get('lift', 1) < 0.3:
        avoid_keywords = [a['term'] for a in avoided[:2]]
        insights.append({
            'category': 'avoidance_pattern',
            'title': '您倾向避开的话题',
            'content': f"您较少关注 {', '.join(avoid_keywords)} 相关论文，这可能反映了您的研究焦点与边界。"
        })

    # 5. 数据质量建议
    counts = report.get('counts', {})
    data_quality = report.get('data_quality', {})
    if data_quality.get('imbalance_severity') in ['severe', 'moderate']:
        insights.append({
            'category': 'recommendation',
            'title': '改进建议',
            'content': f"当前收藏/归档样本较少（{counts['favorites']}篇），建议继续收藏或归档至少50篇论文以提高偏好推断准确性。"
        })

    return insights

def analyze_temporal_trends(favorite_items, hidden_items):
    """分析收藏/隐藏的时间趋势（近12个月）"""
    from collections import defaultdict
    import datetime

    fav_by_month = defaultdict(int)
    hid_by_month = defaultdict(int)

    for item in favorite_items:
        pub_date = item.get('pub_date', '')
        if pub_date:
            try:
                # 处理ISO格式日期
                date = datetime.datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
                month_key = date.strftime('%Y-%m')
                fav_by_month[month_key] += 1
            except:
                pass

    for item in hidden_items:
        pub_date = item.get('pub_date', '')
        if pub_date:
            try:
                date = datetime.datetime.fromisoformat(pub_date.replace('Z', '+00:00'))
                month_key = date.strftime('%Y-%m')
                hid_by_month[month_key] += 1
            except:
                pass

    # 获取所有月份并排序
    all_months = sorted(set(list(fav_by_month.keys()) + list(hid_by_month.keys())))
    recent_months = all_months[-12:] if len(all_months) > 12 else all_months

    trend_data = []
    for month in recent_months:
        fav_count = fav_by_month.get(month, 0)
        hid_count = hid_by_month.get(month, 0)
        total = fav_count + hid_count

        trend_data.append({
            'month': month,
            'favorites': fav_count,
            'hidden': hid_count,
            'total': total,
            'fav_rate': round(fav_count / total, 3) if total > 0 else 0
        })

    return trend_data

def generate_title_report():
    items = paper_service().list_papers("all")
    interactions = paper_service().interactions()
    by_link = {item.get("paper_id"): item for item in items if item.get("paper_id")}

    raw_favorites = interactions.get("favorites") or []
    raw_archived = interactions.get("archived") or []
    raw_hidden = interactions.get("hidden") or []

    favorites = [link for link in raw_favorites if link in by_link]
    archived = [link for link in raw_archived if link in by_link]
    hidden = [link for link in raw_hidden if link in by_link]

    missing_favorites = [link for link in raw_favorites if link not in by_link]
    missing_archived = [link for link in raw_archived if link not in by_link]
    missing_hidden = [link for link in raw_hidden if link not in by_link]

    hidden_set = set(hidden)
    positive_links = []
    positive_seen = set()
    for link in favorites + archived:
        if link in hidden_set or link in positive_seen:
            continue
        positive_links.append(link)
        positive_seen.add(link)

    favorite_items = [by_link[link] for link in positive_links]
    hidden_items = [by_link[link] for link in hidden]

    fav_terms = {}
    hid_terms = {}
    fav_bigrams = {}
    hid_bigrams = {}

    def add_count(counter, key):
        counter[key] = counter.get(key, 0) + 1

    def collect(links, term_counter, bigram_counter):
        for link in links:
            title = by_link[link].get("title") or ""
            tokens = tokenize_title(title)
            for token in tokens:
                add_count(term_counter, token)
            for i in range(len(tokens) - 1):
                add_count(bigram_counter, f"{tokens[i]} {tokens[i + 1]}")

    collect(positive_links, fav_terms, fav_bigrams)
    collect(hidden, hid_terms, hid_bigrams)

    fav_journals = {}
    hid_journals = {}
    fav_sources = {}
    hid_sources = {}
    journal_unknown = 0
    source_unknown = 0

    def collect_meta(items_list, journal_counter, source_counter):
        nonlocal journal_unknown, source_unknown
        for item in items_list:
            journal = clean_journal_name(item.get("journal", ""))
            if journal:
                add_count(journal_counter, journal)
            else:
                journal_unknown += 1

            source = parse_summary_source(item.get("summary", ""))
            if source:
                add_count(source_counter, source)
            else:
                source_unknown += 1

    collect_meta(favorite_items, fav_journals, fav_sources)
    collect_meta(hidden_items, hid_journals, hid_sources)

    # 收集method和topic统计（支持多标签）
    fav_methods = {}
    hid_methods = {}
    fav_topics = {}
    hid_topics = {}

    def extract_labels(item, list_key, single_key):
        raw = item.get(list_key)
        labels = []
        if isinstance(raw, list):
            for entry in raw:
                if isinstance(entry, dict):
                    name = entry.get("name", "")
                    if name:
                        labels.append(name)
                elif isinstance(entry, str):
                    labels.append(entry)
        if not labels:
            value = item.get(single_key, "").strip()
            if value:
                labels.append(value)
        return labels

    for item in favorite_items:
        for method in extract_labels(item, "methods", "method"):
            if method and method not in ["", "Unknown", "Other"]:
                add_count(fav_methods, method)
        for topic in extract_labels(item, "topics", "topic"):
            if topic and topic not in ["", "Unknown", "Other"]:
                add_count(fav_topics, topic)

    for item in hidden_items:
        for method in extract_labels(item, "methods", "method"):
            if method and method not in ["", "Unknown", "Other"]:
                add_count(hid_methods, method)
        for topic in extract_labels(item, "topics", "topic"):
            if topic and topic not in ["", "Unknown", "Other"]:
                add_count(hid_topics, topic)

    def lift_scores(fav_counter, hid_counter, min_total=3):
        """改进的lift计算，添加Wilson置信区间和样本量权重"""
        import math

        keys = set(fav_counter) | set(hid_counter)
        scores = []
        fav_total = sum(fav_counter.values())
        hid_total = sum(hid_counter.values())

        for key in keys:
            fav_val = fav_counter.get(key, 0)
            hid_val = hid_counter.get(key, 0)
            total = fav_val + hid_val

            if total < min_total:
                continue

            # Lift计算
            fav_rate = (fav_val + 1) / (fav_total + len(keys))
            hid_rate = (hid_val + 1) / (hid_total + len(keys))
            lift = fav_rate / hid_rate

            # Wilson置信区间（针对收藏率）
            p = fav_val / total
            n = total
            z = 1.96  # 95%置信区间

            denominator = 1 + z**2 / n
            centre = (p + z**2 / (2*n)) / denominator
            margin = z * math.sqrt((p*(1-p) + z**2/(4*n))/n) / denominator

            ci_lower = max(0, centre - margin)
            ci_upper = min(1, centre + margin)
            ci_width = ci_upper - ci_lower

            # 样本量权重
            sample_weight = math.log(total + 1) / math.log(max(fav_total, hid_total) + 1)

            # 综合置信度
            confidence = (1 - ci_width) * sample_weight

            scores.append((key, lift, fav_val, hid_val, round(confidence, 4)))

        scores.sort(key=lambda x: x[1], reverse=True)
        return scores

    def top_n(counter, limit=20):
        return sorted(counter.items(), key=lambda x: x[1], reverse=True)[:limit]

    journal_lift = lift_scores(fav_journals, hid_journals)
    source_lift = lift_scores(fav_sources, hid_sources)

    term_lift = lift_scores(fav_terms, hid_terms)
    bigram_lift = lift_scores(fav_bigrams, hid_bigrams)

    method_lift = lift_scores(fav_methods, hid_methods, min_total=5)
    topic_lift = lift_scores(fav_topics, hid_topics, min_total=5)

    favorites_clean = [link for link in favorites if link not in hidden_set]
    archived_clean = [link for link in archived if link not in hidden_set]

    report = {
        "generated_at": datetime.datetime.now().isoformat(),
        "source": "title_inference",
        "counts": {
            "favorites": len(positive_links),
            "favorites_only": len(favorites_clean),
            "archived": len(archived_clean),
            "hidden": len(hidden),
            "missing_favorites": len(missing_favorites),
            "missing_archived": len(missing_archived),
            "missing_hidden": len(missing_hidden),
        },
        "title_terms": {
            "top_favorites": [{"term": k, "count": v} for k, v in top_n(fav_terms, 25)],
            "top_hidden": [{"term": k, "count": v} for k, v in top_n(hid_terms, 25)],
            "preferred": [{"term": k, "lift": round(s, 4), "fav": f, "hidden": h, "confidence": c} for k, s, f, h, c in term_lift[:25]],
            "avoided": [{"term": k, "lift": round(s, 4), "fav": f, "hidden": h, "confidence": c} for k, s, f, h, c in term_lift[-25:]],
        },
        "title_bigrams": {
            "preferred": [{"term": k, "lift": round(s, 4), "fav": f, "hidden": h, "confidence": c} for k, s, f, h, c in bigram_lift[:20]],
            "avoided": [{"term": k, "lift": round(s, 4), "fav": f, "hidden": h, "confidence": c} for k, s, f, h, c in bigram_lift[-20:]],
        },
        "missing_links_sample": {
            "favorites": missing_favorites[:10],
            "archived": missing_archived[:10],
            "hidden": missing_hidden[:10],
        },
        "source_journal": {
            "journals": {
                "top_favorites": [{"label": k, "count": v} for k, v in top_n(fav_journals, 20)],
                "top_hidden": [{"label": k, "count": v} for k, v in top_n(hid_journals, 20)],
                "preferred": [{"label": k, "lift": round(s, 4), "fav": f, "hidden": h, "confidence": c} for k, s, f, h, c in journal_lift[:20]],
                "avoided": [{"label": k, "lift": round(s, 4), "fav": f, "hidden": h, "confidence": c} for k, s, f, h, c in journal_lift[-20:]],
            },
            "sources": {
                "top_favorites": [{"label": k, "count": v} for k, v in top_n(fav_sources, 20)],
                "top_hidden": [{"label": k, "count": v} for k, v in top_n(hid_sources, 20)],
                "preferred": [{"label": k, "lift": round(s, 4), "fav": f, "hidden": h, "confidence": c} for k, s, f, h, c in source_lift[:20]],
                "avoided": [{"label": k, "lift": round(s, 4), "fav": f, "hidden": h, "confidence": c} for k, s, f, h, c in source_lift[-20:]],
            },
            "coverage": {
                "journal_unknown": journal_unknown,
                "source_unknown": source_unknown
            }
        },
        "data_quality": generate_data_quality_warnings(len(positive_links), len(hidden)),
        "method_topic": {
            "methods": {
                "top_favorites": [{"label": k, "count": v} for k, v in top_n(fav_methods, 10)],
                "top_hidden": [{"label": k, "count": v} for k, v in top_n(hid_methods, 10)],
                "preferred": [{"label": k, "lift": round(s, 4), "fav": f, "hidden": h, "confidence": c} for k, s, f, h, c in method_lift[:10]],
                "avoided": [{"label": k, "lift": round(s, 4), "fav": f, "hidden": h, "confidence": c} for k, s, f, h, c in method_lift[-10:]],
            },
            "topics": {
                "top_favorites": [{"label": k, "count": v} for k, v in top_n(fav_topics, 10)],
                "top_hidden": [{"label": k, "count": v} for k, v in top_n(hid_topics, 10)],
                "preferred": [{"label": k, "lift": round(s, 4), "fav": f, "hidden": h, "confidence": c} for k, s, f, h, c in topic_lift[:10]],
                "avoided": [{"label": k, "lift": round(s, 4), "fav": f, "hidden": h, "confidence": c} for k, s, f, h, c in topic_lift[-10:]],
            }
        },
        "temporal_trends": analyze_temporal_trends(favorite_items, hidden_items)
    }

    # 生成智能洞察（需要在report之后，因为它依赖report内容）
    report['insights_summary'] = generate_insights_summary(report)

    with FILE_LOCK:
        atomic_write_json(REPORT_FILE, report, ensure_ascii=True)

    return {"status": "ok", "report": report}

def load_journal_meta():
    if not os.path.exists(JOURNALS_META_FILE):
        return {}
    try:
        with open(JOURNALS_META_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            cleaned = {}
            for key, value in data.items():
                if isinstance(value, str):
                    cleaned[key] = {"subject": value}
                elif isinstance(value, dict):
                    item = {}
                    subject = value.get("subject")
                    name = value.get("name")
                    if isinstance(subject, str) and subject.strip():
                        item["subject"] = subject
                    if isinstance(name, str) and name.strip():
                        item["name"] = name
                    if item:
                        cleaned[key] = item
            return cleaned
    except:
        return {}
    return {}

def save_journal_meta(meta):
    try:
        with FILE_LOCK:
            atomic_write_json(JOURNALS_META_FILE, meta)
    except:
        pass

def strip_tracking_params(url):
    """Remove utm_* query parameters, leaving the rest of the URL byte-identical."""
    if not isinstance(url, str):
        return url
    value = url.strip()
    if "?" not in value:
        return value
    base, _, rest = value.partition("?")
    query, hash_mark, fragment = rest.partition("#")
    kept = [part for part in query.split("&")
            if part and not part.split("=", 1)[0].lower().startswith("utm_")]
    result = base + ("?" + "&".join(kept) if kept else "")
    return result + (hash_mark + fragment if hash_mark else "")


def parse_rss_catalog(path=None):
    """Parse `RSS list.md` into [{name, url, subject, tags}] in file order.

    Format: `## Subject`, then `- Journal name`, an optional `标签:`/`Tags:` line
    (comma separated, full- or half-width), and an `RSS: \\`url\\`` line.
    """
    path = path or RSS_LIST_FILE
    if not os.path.exists(path):
        return []
    items = []
    current_subject = ""
    pending_name = ""
    pending_tags = []
    with open(path, 'r', encoding='utf-8') as f:
        for raw in f:
            line = raw.strip()
            if line.startswith("## "):
                current_subject = line[3:].strip()
                pending_name, pending_tags = "", []
                continue
            if line.startswith("- "):
                pending_name, pending_tags = line[2:].strip(), []
                continue
            tag_match = re.match(r"^(?:标签|tags?)\s*[:：]\s*(.*)$", line, re.IGNORECASE)
            if tag_match:
                pending_tags = [tag.strip() for tag in re.split(r"[，,;；]", tag_match.group(1)) if tag.strip()]
                continue
            if "RSS:" in line and "`" in line:
                start = line.find("`")
                end = line.rfind("`")
                if start != -1 and end > start:
                    url = line[start + 1:end].strip()
                    if url and pending_name:
                        items.append({"name": pending_name, "url": url,
                                      "subject": current_subject, "tags": list(pending_tags)})
                pending_name, pending_tags = "", []
    return items


def load_subscribed_journals():
    if not os.path.exists(JOURNALS_FILE):
        return []
    try:
        with open(JOURNALS_FILE, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip() and not line.strip().startswith("#")]
    except Exception:
        return []


def journal_catalog():
    subscribed = {strip_tracking_params(url) for url in load_subscribed_journals()}
    items = []
    for entry in parse_rss_catalog():
        entry = dict(entry)
        entry["subscribed"] = strip_tracking_params(entry["url"]) in subscribed
        items.append(entry)
    return {"items": items}


def load_rss_list_meta():
    meta = {}
    try:
        for entry in parse_rss_catalog():
            item = {"name": entry["name"]}
            if entry.get("subject"):
                item["subject"] = entry["subject"]
            meta[entry["url"]] = item
    except Exception:
        return {}
    return meta


def read_keywords_text():
    if not os.path.exists(KEYWORDS_FILE):
        return ""
    with open(KEYWORDS_FILE, 'r', encoding='utf-8') as f:
        return f.read()


def keywords_payload(text):
    lines = [line.strip() for line in (text or "").splitlines()]
    payload = {"text": text or "",
               "keywords": [line for line in lines if line and not line.startswith("#")]}
    # Extra, informational: GitHub Actions/CLI use RSS_KEYWORDS instead of the file.
    payload["env_override"] = bool(os.environ.get("RSS_KEYWORDS", "").strip())
    return payload


def save_keywords_text(text):
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    if normalized and not normalized.endswith("\n"):
        normalized += "\n"
    with FILE_LOCK:
        atomic_write_text(KEYWORDS_FILE, normalized)
    return normalized


def keyword_corpus():
    """Stored SQLite papers as {paper_id, title, text} for keyword previews."""
    from paper_feed.exporter import database_items
    service = paper_service()
    service._ensure_database()
    papers = []
    for item in database_items(service.database):
        abstract = item.get("abstract") if isinstance(item.get("abstract"), dict) else {}
        parts = [item.get("title") or "", item.get("summary") or "",
                 abstract.get("raw_abstract") or "", abstract.get("abstract") or ""]
        papers.append({"paper_id": item.get("paper_id"), "title": item.get("title") or "",
                       "text": " ".join(part for part in parts if isinstance(part, str))})
    return papers


def keyword_preview(text):
    if not isinstance(text, str):
        raise ValueError("text must be a string")
    queries = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    return preview_keywords(queries, keyword_corpus())

def load_categories():
    if not os.path.exists(CATEGORIES_FILE):
        return {}
    try:
        with open(CATEGORIES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_categories(payload):
    with FILE_LOCK:
        atomic_write_json(CATEGORIES_FILE, payload)

def load_user_corrections():
    if not os.path.exists(USER_CORRECTIONS_FILE):
        return {}
    try:
        with open(USER_CORRECTIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_user_corrections(payload):
    with FILE_LOCK:
        atomic_write_json(USER_CORRECTIONS_FILE, payload)

def normalize_label_entries(raw_entries):
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
        confidence = max(0.0, min(1.0, confidence))
        entries.append({"name": name, "confidence": confidence})
    entries.sort(key=lambda x: x.get("confidence", 0), reverse=True)
    return entries

def update_feed_item_classification(item_id, updated):
    if not os.path.exists(FEED_FILE):
        return
    try:
        with open(FEED_FILE, 'r', encoding='utf-8') as f:
            feed = json.load(f)
    except:
        return
    changed = False
    for item in feed.get("items", []):
        if item.get("id") == item_id:
            item.update(updated)
            changed = True
            break
    if changed:
        with FILE_LOCK:
            atomic_write_json(FEED_FILE, feed, ensure_ascii=True)

def config_sources():
    """Where each effective setting comes from: env > config.json > default.

    Mirrors get_RSS.get_config precedence; returns labels only, never values.
    """
    import get_RSS
    local_config = {}
    if os.path.exists(get_RSS.CONFIG_FILE):
        try:
            with open(get_RSS.CONFIG_FILE, "r", encoding="utf-8") as handle:
                loaded = json.load(handle)
            if isinstance(loaded, dict):
                local_config = loaded
        except Exception:
            local_config = {}
    sources = {}
    for key in CONFIG_SAVE_KEYS:
        if get_RSS.is_usable_config_value(os.environ.get(key)):
            sources[key] = "env"
        elif get_RSS.is_usable_config_value(local_config.get(key)):
            sources[key] = "config"
        elif get_RSS.CONFIG_DEFAULTS.get(key):
            sources[key] = "default"
        else:
            sources[key] = "unset"
    return sources


def _redact(text, secret):
    text = str(text or "")
    if secret:
        text = text.replace(secret, "***")
    return text[:400]


def validate_config_updates(updates):
    """Normalize/validate the non-secret AI backend settings; raises ValueError."""
    import get_RSS
    if "AI_BACKEND" in updates:
        backend = updates["AI_BACKEND"].lower()
        if backend and backend not in get_RSS.AI_BACKENDS:
            raise ValueError(f"AI_BACKEND must be one of {', '.join(get_RSS.AI_BACKENDS)}")
        updates["AI_BACKEND"] = backend
    for key in ("CODEX_MODEL", "CODEX_REASONING_EFFORT"):
        value = updates.get(key)
        if value and not get_RSS.is_safe_codex_token(value):
            raise ValueError(f"{key} may only contain letters, digits and . _ : / -")
    if any(ch in updates.get("CODEX_PATH", "") for ch in '"\r\n'):
        raise ValueError("CODEX_PATH must be a plain file path")
    return updates


def ai_status(config=None):
    """Public (secret-free) summary of the configured and effective AI backend."""
    import get_RSS
    config = get_config() if config is None else config
    settings = get_RSS.ai_settings(config)
    return {
        "AI_BACKEND": settings["configured_backend"],
        "effective_backend": settings["backend"],
        "CODEX_MODEL": config.get("CODEX_MODEL") or get_RSS.DEFAULT_CODEX_MODEL,
        "CODEX_REASONING_EFFORT": config.get("CODEX_REASONING_EFFORT") or get_RSS.DEFAULT_CODEX_REASONING_EFFORT,
        "CODEX_PATH": config.get("CODEX_PATH") or "",
        "codex_available": bool(settings["codex_available"]),
        "ai_ready": bool(settings["ready"]),
        "ai_model": settings["model"],
        "ai_reason": settings["reason"],
    }


def check_ai_connection():
    """Send one minimal request through the effective AI backend (no retries).

    Returns ``{ok, backend, model, latency_ms, error?}``.
    """
    import time
    import get_RSS
    config = get_config()
    settings = get_RSS.ai_settings(config)
    backend = settings["backend"]
    model = settings["model"] or config.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL
    if not settings["ready"]:
        return {"ok": False, "backend": None, "model": model, "latency_ms": None,
                "error": "未配置可用的 AI 后端（未找到 Codex CLI，也未配置 API Key）。 / "
                         f"No AI backend available: {settings['reason']}"}
    secret = config.get("OPENAI_API_KEY")
    started = time.perf_counter()
    try:
        client = get_RSS.make_openai_client(settings["api_key"], settings["base_url"], settings["proxy"])
        if backend == "codex":
            client = client.with_options(max_retries=0, timeout=CODEX_TEST_CONNECTION_TIMEOUT)
            client.chat.completions.create(model=model, messages=[{"role": "user", "content": "Reply with: ok"}])
        else:
            client = client.with_options(max_retries=0, timeout=TEST_CONNECTION_TIMEOUT)
            client.chat.completions.create(model=model, max_tokens=1,
                                           messages=[{"role": "user", "content": "ping"}])
    except Exception as error:
        latency = int((time.perf_counter() - started) * 1000)
        return {"ok": False, "backend": backend, "model": model, "latency_ms": latency,
                "error": f"连接失败 / Connection failed: {type(error).__name__}: {_redact(error, secret)}"}
    return {"ok": True, "backend": backend, "model": model,
            "latency_ms": int((time.perf_counter() - started) * 1000)}


# Backward-compatible name.
check_openai_connection = check_ai_connection



def pending_summary_counts():
    """{pending, total_favorites}: favorites the summarize job would still process."""
    from get_RSS import pending_summary_items
    service = paper_service()
    service._ensure_database()
    favorites = service.favorite_legacy_ids()
    legacy_ids = [legacy_id for _, legacy_id in favorites if legacy_id]
    pending = pending_summary_items(service.database, legacy_ids) if legacy_ids else []
    return {"pending": len(pending), "total_favorites": len(favorites)}


def is_http_url(value):
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme.lower() in {"http", "https"} and bool(parsed.netloc)


def probe_journal_feed(url):
    """Fetch one RSS URL once and summarize what was parsed."""
    from get_RSS import fetch_rss_result
    result = fetch_rss_result(url, retries=1) or {}
    entries = result.get("entries") or []
    payload = {"ok": bool(result.get("success")) and bool(entries), "entries": len(entries),
               "feed_title": (entries[0].get("journal") if entries else "") or "",
               "latest_titles": [entry.get("title") or "" for entry in entries[:3]],
               "status_code": result.get("status_code")}
    if not result.get("success"):
        payload["error"] = f"抓取失败 / Fetch failed: {result.get('error') or 'unknown error'}"
    elif not entries:
        payload["error"] = "未解析到任何条目，可能不是 RSS 地址。 / No entries parsed; this may not be an RSS feed."
    return payload


LOCAL_HOSTNAMES = {"127.0.0.1", "localhost", "::1"}
WILDCARD_HOSTS = {"", "0.0.0.0", "::"}


def _host_name(host_header):
    """Hostname part of a Host header ("[::1]:8000" -> "::1")."""
    host = (host_header or "").strip().lower()
    if host.startswith("["):
        return host[1:host.find("]")] if "]" in host else host[1:]
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # 设置静态文件根目录为 web/
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def send_json(self, status_code, payload):
        body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
        self.send_response(status_code)
        self.send_header('Content-type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def send_error(self, code, message=None, explain=None):
        # API clients always receive JSON; static files keep the default page.
        if urlparse(self.path).path.startswith('/api/'):
            try:
                self.send_json(code, {"status": "error", "message": message or http.HTTPStatus(code).phrase})
            except Exception:
                pass
            return
        super().send_error(code, message, explain)

    def read_json_body(self):
        """Parse a JSON request body; a missing Content-Length means an empty body."""
        raw_length = self.headers.get('Content-Length')
        try:
            length = int(raw_length) if raw_length else 0
        except ValueError:
            raise ValueError("Invalid Content-Length header")
        if length <= 0:
            return {}
        data = self.rfile.read(length)
        if not data.strip():
            return {}
        return json.loads(data.decode('utf-8'))

    def _guarded(self, handler):
        try:
            handler()
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as error:
            print(f"Unhandled error for {self.command} {self.path}: {error}")
            try:
                self.send_json(500, {"status": "error", "message": str(error)[:400]})
            except Exception:
                pass

    def _server_port(self):
        try:
            return int(self.server.server_address[1])
        except Exception:
            return None

    def _allowed_hostnames(self):
        allowed = set(LOCAL_HOSTNAMES)
        try:
            bound = str(self.server.server_address[0]).lower()
        except Exception:
            bound = ""
        if bound and bound not in WILDCARD_HOSTS:
            allowed.add(bound)
        return allowed

    def _reject(self, status, message):
        self.close_connection = True
        self.send_json(status, {"status": "error", "message": message})
        return True

    def _security_rejection(self):
        """Return True when a response has been sent and the request must stop.

        The server has no authentication, so it defends against DNS rebinding
        (Host check) and cross-site requests (Origin / Content-Type checks).
        """
        host = self.headers.get("Host")
        if host is not None and _host_name(host) not in self._allowed_hostnames():
            return self._reject(403, "Forbidden host / 不允许的 Host")
        if self.command != "POST":
            return False
        fetch_site = (self.headers.get("Sec-Fetch-Site") or "").lower()
        if fetch_site in {"cross-site", "same-site"}:
            return self._reject(403, "Cross-site request rejected / 拒绝跨站请求")
        origin = self.headers.get("Origin")
        if origin is not None:
            port = self._server_port()
            allowed = {f"http://{name}:{port}" for name in ("127.0.0.1", "localhost", "[::1]")}
            if origin.strip().lower().rstrip("/") not in allowed:
                return self._reject(403, "Cross-origin request rejected / 拒绝跨源请求")
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length) if raw_length else 0
        except ValueError:
            return self._reject(400, "Invalid Content-Length header")
        if length < 0:
            return self._reject(400, "Invalid Content-Length header")
        if length > MAX_REQUEST_BODY_BYTES:
            return self._reject(413, "Request body too large (limit 2 MB) / 请求体过大")
        content_type = self.headers.get("Content-Type")
        media_type = (content_type or "").split(";", 1)[0].strip().lower()
        # Bodies must be JSON (forms and text/plain can be sent cross-site
        # without a CORS preflight).  A body-less POST without Content-Type is
        # tolerated for simple action endpoints; Origin is still enforced above.
        if (content_type is not None or length > 0) and media_type != "application/json":
            return self._reject(415, "Content-Type must be application/json")
        return False

    def do_GET(self):
        if self._security_rejection():
            return
        self._guarded(self._do_get)

    def do_HEAD(self):
        if self._security_rejection():
            return
        super().do_HEAD()

    def do_POST(self):
        if self._security_rejection():
            return
        self._guarded(self._do_post)

    def _do_get(self):
        # 解析路径，忽略 query parameters
        parsed = urlparse(self.path)
        path = parsed.path

        if path == '/api/papers':
            try:
                view = parse_qs(parsed.query).get("view", ["inbox"])[0]
                self.send_json(200, {"items": paper_service().list_papers(view), "view": view})
            except ValueError as error:
                self.send_json(400, {"status": "error", "message": str(error)})
            return

        if path.startswith('/api/papers/'):
            paper_id = path[len('/api/papers/'):]
            if '/' not in paper_id and paper_id:
                item = paper_service().get_paper(paper_id)
                self.send_json(200 if item else 404, item or {"status": "error", "message": "paper_id not found"})
                return

        # 添加一个 API 来获取当前配置（用于回显到前端）
        if path == '/api/config':
            config = get_config()
            has_key = bool(config.get("OPENAI_API_KEY"))
            # Never return the key itself to the browser.
            safe_config = {
                "api_key_configured": has_key,
                "has_api_key": has_key,
                "OPENAI_BASE_URL": config.get("OPENAI_BASE_URL") or "",
                "OPENAI_PROXY": config.get("OPENAI_PROXY") or "",
                "OPENAI_MODEL": config.get("OPENAI_MODEL") or DEFAULT_OPENAI_MODEL,
                "sources": config_sources(),
            }
            safe_config.update(ai_status(config))
            self.send_json(200, safe_config)
            return

        if path == '/api/summarize_favorites/pending':
            self.send_json(200, pending_summary_counts())
            return

        if path in ('/api/jobs', '/api/jobs/'):
            self.send_json(200, {"jobs": JOB_RUNNER.list()})
            return

        if path == '/api/keywords':
            self.send_json(200, keywords_payload(read_keywords_text()))
            return

        if path == '/api/journal_catalog':
            self.send_json(200, journal_catalog())
            return

        if path.startswith('/api/jobs/'):
            job = JOB_RUNNER.get(path.rsplit('/', 1)[-1])
            if not job:
                self.send_json(404, {"status": "error", "message": "Job not found"})
            else:
                self.send_json(200, job)
            return

        if path == '/api/journals':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()

            journals = load_subscribed_journals()
            meta = load_journal_meta()
            meta = {k: v for k, v in meta.items() if k in set(journals)}
            rss_meta = load_rss_list_meta()
            merged = {}
            for url in journals:
                base = meta.get(url, {})
                supplement = rss_meta.get(url, {})
                subject = base.get("subject") or supplement.get("subject")
                name = base.get("name") or supplement.get("name")
                entry = {}
                if subject:
                    entry["subject"] = subject
                if name:
                    entry["name"] = name
                if entry:
                    merged[url] = entry
            self.wfile.write(json.dumps({"journals": journals, "meta": merged}).encode('utf-8'))
            return

        if path == '/api/interactions':
            self.send_json(200, paper_service().interactions())
            return

        if path == '/api/preference_report':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            if os.path.exists(REPORT_FILE):
                try:
                    with open(REPORT_FILE, 'r', encoding='utf-8') as f:
                        self.wfile.write(f.read().encode('utf-8'))
                except:
                    self.wfile.write(b'{"status": "error", "message": "Failed to read report"}')
            else:
                self.wfile.write(b'{"status": "error", "message": "Report not generated"}')
            return

        if path == '/api/categories':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            payload = load_categories() or {}
            self.wfile.write(json.dumps(payload, ensure_ascii=False).encode('utf-8'))
            return

        # 特殊处理 feed.json - 禁用缓存
        if path == '/feed.json':
            file_path = os.path.join(WEB_DIR, 'feed.json')
            if os.path.exists(file_path):
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
                self.send_header('Pragma', 'no-cache')
                self.send_header('Expires', '0')
                self.end_headers()
                with open(file_path, 'rb') as f:
                    self.wfile.write(f.read())
                return

        if path.startswith('/api/'):
            self.send_json(404, {"status": "error", "message": "Endpoint not found"})
            return

        return super().do_GET()

    def _do_post(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == '/api/keywords':
            try:
                req_data = self.read_json_body()
                if not isinstance(req_data, dict) or not isinstance(req_data.get("text"), str):
                    raise ValueError('Body must be {"text": "..."}')
                self.send_json(200, keywords_payload(save_keywords_text(req_data["text"])))
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json(400, {"status": "error", "message": str(error)})
            return
        if path == '/api/keywords/preview':
            try:
                req_data = self.read_json_body()
                if not isinstance(req_data, dict) or not isinstance(req_data.get("text"), str):
                    raise ValueError('Body must be {"text": "..."}')
                self.send_json(200, keyword_preview(req_data["text"]))
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json(400, {"status": "error", "message": str(error)})
            return
        if path == '/api/export_favorites_ris':
            try:
                result = build_favorites_ris()
                timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
                self.send_response(200)
                self.send_header('Content-type', 'application/x-research-info-systems; charset=utf-8')
                self.send_header('Content-Disposition', f'attachment; filename="paper-feed-favorites-{timestamp}.ris"')
                self.send_header('Cache-Control', 'no-store')
                self.send_header('X-Paper-Feed-Exported', str(result["count"]))
                self.end_headers()
                self.wfile.write(result["ris"].encode('utf-8'))
            except Exception as error:
                # A download failure is a read-only operation; return a stable
                # JSON error shape and leave SQLite untouched.
                self.send_json(500, {"status": "error", "message": "RIS export failed", "detail": str(error)[:400]})
            return
        if path.startswith('/api/papers/') and path.endswith('/review'):
            paper_id = path[len('/api/papers/'):-len('/review')].strip('/')
            try:
                req_data = self.read_json_body()
                item = paper_service().review(paper_id, req_data.get("action"))
                self.send_json(200, {"status": "ok", "paper": item, "interactions": paper_service().interactions()})
            except PaperNotFound as error:
                self.send_json(404, {"status": "error", "message": str(error)})
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json(400, {"status": "error", "message": str(error)})
            return
        if path == '/api/interactions':
            try:
                req_data = self.read_json_body()
                data = apply_interaction_change(req_data)
                self.send_json(200, data)
                return
            except PaperReferenceError as e:
                self.send_json(400, {"status": "error", "message": str(e)})
            except PaperNotFound as e:
                self.send_json(404, {"status": "error", "message": str(e)})
            except (ValueError, json.JSONDecodeError) as e:
                self.send_json(400, {"status": "error", "message": str(e)})
            return

        if path == '/api/summarize_favorites':
            job, duplicate = JOB_RUNNER.enqueue("summarize", run_summarize_job)
            self.send_json(202, {"job": job, "duplicate": duplicate})
            return

        if path == '/api/preference_report':
            print("Received preference report request...")
            try:
                result = generate_title_report()
                status_code = 200 if result.get("status") == "ok" else 500
                self.send_json(status_code, result)
            except Exception as e:
                print(f"Preference report error: {e}")
                self.send_json(400 if isinstance(e, ValueError) else 500, {"status": "error", "message": str(e)})
            return

        if path == '/api/update_abstract':
            try:
                req_data = self.read_json_body()
                item_id = paper_service().resolve_reference(req_data)
                new_abstract = req_data.get("abstract")
                
                if not item_id or new_abstract is None:
                    raise ValueError("Missing id or abstract")
                if not isinstance(new_abstract, str):
                    raise ValueError("abstract must be a string")
                if len(new_abstract) > MAX_ABSTRACT_CHARS:
                    raise ValueError(f"Abstract too long (max {MAX_ABSTRACT_CHARS} characters) / 摘要过长")
                
                paper_service().save_abstract(item_id, new_abstract)
                
                self.send_json(200, {"status": "ok", "message": "Abstract updated"})
            except PaperNotFound as e:
                self.send_json(404, {"status": "error", "message": str(e)})
            except (PaperReferenceError, ValueError) as e:
                self.send_json(400, {"status": "error", "message": str(e)})
            except Exception as e:
                print(f"Update abstract error: {e}")
                self.send_json(400 if isinstance(e, ValueError) else 500, {"status": "error", "message": str(e)})
            return

        if path == '/api/update_classification':
            try:
                req_data = self.read_json_body()
                item_id = paper_service().resolve_reference(req_data)
                if not item_id:
                    raise ValueError("Missing id")

                methods = normalize_label_entries(req_data.get("methods", []))
                topics = normalize_label_entries(req_data.get("topics", []))
                theories = [t for t in (req_data.get("theories") or []) if isinstance(t, str)]
                context = [t for t in (req_data.get("context") or []) if isinstance(t, str)]
                subjects = [t for t in (req_data.get("subjects") or []) if isinstance(t, str)]
                novelty_score = req_data.get("novelty_score")

                correction = {
                    "methods": methods,
                    "topics": topics,
                    "theories": theories,
                    "context": context,
                    "subjects": subjects,
                    "novelty_score": novelty_score,
                    "updated_at": datetime.datetime.now().isoformat(),
                }
                categories = load_categories() or {}
                method_names = [m.get("name") for m in categories.get("methods", []) if isinstance(m, dict)]
                topic_names = [t.get("name") for t in categories.get("topics", []) if isinstance(t, dict)]
                correction.update({"method": methods[0]["name"] if methods else fallback_label(method_names, "Qualitative"),
                                   "topic": topics[0]["name"] if topics else fallback_label(topic_names, "Other Marketing"),
                                   "classification_source": "user", "user_corrected": True})
                paper_service().save_classification(item_id, correction)

                self.send_json(200, {"status": "ok", "message": "Classification updated"})
            except PaperNotFound as e:
                self.send_json(404, {"status": "error", "message": str(e)})
            except (PaperReferenceError, ValueError) as e:
                self.send_json(400, {"status": "error", "message": str(e)})
            except Exception as e:
                print(f"Update classification error: {e}")
                self.send_json(400 if isinstance(e, ValueError) else 500, {"status": "error", "message": str(e)})
            return

        if path == '/api/categories':
            try:
                req_data = self.read_json_body()
                if not isinstance(req_data, dict):
                    raise ValueError("Invalid categories payload")
                save_categories(req_data)
                self.send_json(200, {"status": "ok", "message": "Categories saved"})
            except Exception as e:
                self.send_json(400 if isinstance(e, ValueError) else 500, {"status": "error", "message": str(e)})
            return

        if path == '/api/save_config':
            
            try:
                new_config = self.read_json_body()
                if not isinstance(new_config, dict):
                    raise ValueError("Config payload must be a JSON object")
                # 读取旧配置以合并（如果有其他字段）
                current_config = {}
                if os.path.exists(CONFIG_FILE):
                    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                        loaded = json.load(f)
                    if isinstance(loaded, dict):
                        current_config = loaded

                # Only known settings are persisted; unknown keys are ignored.
                updates = {key: new_config[key].strip() for key in CONFIG_SAVE_KEYS
                           if isinstance(new_config.get(key), str)}
                # A blank password field means "keep the existing key" because
                # read APIs deliberately never return secrets to the browser.
                if not updates.get("OPENAI_API_KEY"):
                    updates.pop("OPENAI_API_KEY", None)
                validate_config_updates(updates)
                current_config.update(updates)
                if new_config.get("clear_api_key") is True:
                    current_config.pop("OPENAI_API_KEY", None)
                
                # 写入文件
                # 这里的 CONFIG_FILE 是在根目录下，不是 web/ 下，更安全
                with FILE_LOCK:
                    atomic_write_json(CONFIG_FILE, current_config)
                
                self.send_json(200, {"status": "ok", "message": "Config saved"})
                
            except Exception as e:
                self.send_json(400 if isinstance(e, ValueError) else 500, {"status": "error", "message": str(e)})
            return

        if path == '/api/journals':
            try:
                req_data = self.read_json_body()
                journals = req_data.get("journals", [])
                if not isinstance(journals, list):
                    raise ValueError("Invalid journals payload")
                meta_payload = req_data.get("meta", None)

                invalid = [item.strip() for item in journals
                           if isinstance(item, str) and item.strip() and not is_http_url(item)]
                if invalid:
                    raise ValueError("Only http(s) RSS URLs are allowed / 仅支持 http(s) 地址: "
                                     + ", ".join(invalid[:5]))
                cleaned = []
                seen = set()
                for item in journals:
                    if not isinstance(item, str):
                        continue
                    value = strip_tracking_params(item.strip())
                    if value and value not in seen:
                        cleaned.append(value)
                        seen.add(value)

                with FILE_LOCK:
                    atomic_write_text(JOURNALS_FILE, "\n".join(cleaned) + ("\n" if cleaned else ""))

                meta = {}
                if meta_payload is None:
                    meta = load_journal_meta()
                elif isinstance(meta_payload, dict):
                    for raw_key, value in meta_payload.items():
                        key = strip_tracking_params(raw_key)
                        if isinstance(value, str):
                            meta[key] = {"subject": value}
                        elif isinstance(value, dict):
                            item = {}
                            subject = value.get("subject")
                            name = value.get("name")
                            if isinstance(subject, str) and subject.strip():
                                item["subject"] = subject
                            if isinstance(name, str) and name.strip():
                                item["name"] = name
                            if item:
                                meta[key] = item
                else:
                    raise ValueError("Invalid meta payload")

                cleaned_meta = {}
                cleaned_set = set(cleaned)
                for url, item in meta.items():
                    if url not in cleaned_set:
                        continue
                    if not isinstance(item, dict):
                        continue
                    subject = item.get("subject", "")
                    name = item.get("name", "")
                    meta_item = {}
                    if isinstance(subject, str) and subject.strip():
                        meta_item["subject"] = subject.strip()
                    if isinstance(name, str) and name.strip():
                        meta_item["name"] = name.strip()
                    if meta_item:
                        cleaned_meta[url] = meta_item
                save_journal_meta(cleaned_meta)

                self.send_json(200, {
                    "status": "ok",
                    "journals": cleaned,
                    "meta": cleaned_meta
                })
            except Exception as e:
                self.send_json(400 if isinstance(e, ValueError) else 500, {"status": "error", "message": str(e)})
            return

        if path == '/api/journals/test':
            try:
                req_data = self.read_json_body()
                url = req_data.get("url") if isinstance(req_data, dict) else None
                if not is_http_url(url):
                    raise ValueError("url must be an http(s) URL / 仅支持 http(s) 地址")
                self.send_json(200, probe_journal_feed(url.strip()))
            except (ValueError, json.JSONDecodeError) as error:
                self.send_json(400, {"status": "error", "message": str(error)})
            return

        if path == '/api/test_connection':
            self.send_json(200, check_ai_connection())
            return

        if path == '/api/reanalyze':
            job, duplicate = JOB_RUNNER.enqueue("reanalyze", run_reanalysis_job)
            self.send_json(202, {"job": job, "duplicate": duplicate})
            return

        if path == '/api/fetch':
            job, duplicate = JOB_RUNNER.enqueue("fetch", run_fetch_job)
            self.send_json(202, {"job": job, "duplicate": duplicate})
            return

        # 如果不是上述 API，返回 404
        self.send_json(404, {"status": "error", "message": "Endpoint not found"})
        return

class PaperFeedHTTPServer(http.server.ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second process bind an in-use port, which
    # would silently split requests between two servers.  POSIX keeps it so a
    # restart does not wait for TIME_WAIT.
    allow_reuse_address = os.name != "nt"
    daemon_threads = True


def resolve_port(cli_port=None):
    """--port > PAPER_FEED_PORT > 8000."""
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
        print(f"Warning: ignoring invalid PAPER_FEED_PORT={env_port!r}; using {DEFAULT_PORT}.")
    return DEFAULT_PORT


def _is_address_in_use(error):
    return (getattr(error, "errno", None) in {errno.EADDRINUSE, 10048}
            or getattr(error, "winerror", None) in {10048, 10013})


LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def browser_url(host, port):
    """URL a local browser should open for a server bound to *host*."""
    if host in {"", "0.0.0.0", "::"} or host in LOOPBACK_HOSTS:
        host = "127.0.0.1"
    return f"http://{host}:{port}/"


def run_server(port=None, host="127.0.0.1", on_ready=None):
    """Serve until Ctrl+C.  *on_ready(url)* runs once the port is bound."""
    port = resolve_port(port) if port is None else int(port)
    host = host or "127.0.0.1"
    try:
        httpd = PaperFeedHTTPServer((host, port), CustomHandler)
    except OSError as error:
        if _is_address_in_use(error):
            print(f"Error: port {port} is already in use on {host}.")
            print(f"  - If Paper Feed is already running, open http://127.0.0.1:{port}/ instead "
                  "(or run `python -m paper_feed start`, which detects it).")
            print("  - Otherwise stop the other program, or choose another port with "
                  "`python -m paper_feed serve --port 8001` or the PAPER_FEED_PORT environment variable.")
            return 1
        raise
    with httpd:
        if host not in LOOPBACK_HOSTS:
            print(f"WARNING: listening on {host}. The API has no authentication and can modify local "
                  "files; do not expose it to an untrusted network.")
        print(f"Server started at {browser_url(host, port)}")
        print("Press Ctrl+C to stop.")
        if on_ready is not None:
            on_ready(browser_url(host, port))
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server...")
    return 0


def main(argv=None):
    """Compatibility entry point: `python server.py` == `python -m paper_feed serve`."""
    from paper_feed.cli import main as cli_main
    return cli_main(["serve", *(sys.argv[1:] if argv is None else argv)])


if __name__ == "__main__":
    # Share this module object with the CLI instead of importing the file twice
    # (a second copy would start a second job worker).
    sys.modules.setdefault("server", sys.modules[__name__])
    sys.exit(main())
