import http.server
import socketserver
import json
import os
import sys
import datetime
import html
import re
from functools import partial

# 导入 RSS 抓取逻辑
# 确保 get_RSS.py 在同一目录下
try:
    from get_RSS import run_rss_flow, get_config, summarize_specific_papers, load_abstracts, save_abstracts
except ImportError:
    print("Error: Could not import run_rss_flow from get_RSS.py")
    sys.exit(1)

PORT = 8000
WEB_DIR = "web"
CONFIG_FILE = "config.json"
INTERACTIONS_FILE = os.path.join(WEB_DIR, "interactions.json")
FEED_FILE = os.path.join(WEB_DIR, "feed.json")
REPORT_FILE = os.path.join(WEB_DIR, "preference_report.json")
CATEGORIES_FILE = os.path.join(WEB_DIR, "categories.json")
USER_CORRECTIONS_FILE = os.path.join(WEB_DIR, "user_corrections.json")
JOURNALS_FILE = "journals.dat"
JOURNALS_META_FILE = "journals_meta.json"
RSS_LIST_FILE = "RSS list.md"

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
    if not os.path.exists(FEED_FILE):
        return {"status": "error", "message": "feed.json not found"}
    if not os.path.exists(INTERACTIONS_FILE):
        return {"status": "error", "message": "interactions.json not found"}

    with open(FEED_FILE, 'r', encoding='utf-8') as f:
        feed = json.load(f)
    with open(INTERACTIONS_FILE, 'r', encoding='utf-8') as f:
        interactions = json.load(f)

    items = feed.get("items", [])
    by_link = {item.get("link"): item for item in items if item.get("link")}

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

    with open(REPORT_FILE, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=True)

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
        with open(JOURNALS_META_FILE, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except:
        pass

def load_rss_list_meta():
    if not os.path.exists(RSS_LIST_FILE):
        return {}
    meta = {}
    current_subject = ""
    pending_name = ""
    try:
        with open(RSS_LIST_FILE, 'r', encoding='utf-8') as f:
            for raw in f:
                line = raw.strip()
                if line.startswith("## "):
                    current_subject = line[3:].strip()
                    pending_name = ""
                    continue
                if line.startswith("- "):
                    pending_name = line[2:].strip()
                    continue
                if "RSS:" in line and "`" in line:
                    start = line.find("`")
                    end = line.rfind("`")
                    if start != -1 and end > start:
                        url = line[start + 1:end].strip()
                        if url and pending_name:
                            entry = {}
                            if current_subject:
                                entry["subject"] = current_subject
                            entry["name"] = pending_name
                            meta[url] = entry
                    pending_name = ""
    except:
        return {}
    return meta

def load_categories():
    if not os.path.exists(CATEGORIES_FILE):
        return {}
    try:
        with open(CATEGORIES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_categories(payload):
    with open(CATEGORIES_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

def load_user_corrections():
    if not os.path.exists(USER_CORRECTIONS_FILE):
        return {}
    try:
        with open(USER_CORRECTIONS_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except:
        return {}

def save_user_corrections(payload):
    with open(USER_CORRECTIONS_FILE, 'w', encoding='utf-8') as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)

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
        with open(FEED_FILE, 'w', encoding='utf-8') as f:
            json.dump(feed, f, ensure_ascii=True, indent=2)

class CustomHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        # 设置静态文件根目录为 web/
        super().__init__(*args, directory=WEB_DIR, **kwargs)

    def do_GET(self):
        # 解析路径，忽略 query parameters
        path = self.path.split('?')[0]

        # 添加一个 API 来获取当前配置（用于回显到前端）
        if path == '/api/config':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()

            config = get_config()
            # 处于安全考虑，返回时可以脱敏，或者因为是本地运行，直接返回方便编辑
            # 这里直接返回
            self.wfile.write(json.dumps(config).encode('utf-8'))
            return

        if path == '/api/journals':
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()

            journals = []
            if os.path.exists(JOURNALS_FILE):
                try:
                    with open(JOURNALS_FILE, 'r', encoding='utf-8') as f:
                        journals = [line.strip() for line in f if line.strip()]
                except:
                    journals = []
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
            self.send_response(200)
            self.send_header('Content-type', 'application/json')
            self.send_header('Cache-Control', 'no-cache, no-store, must-revalidate')
            self.send_header('Pragma', 'no-cache')
            self.send_header('Expires', '0')
            self.end_headers()
            if os.path.exists(INTERACTIONS_FILE):
                try:
                    with open(INTERACTIONS_FILE, 'r', encoding='utf-8') as f:
                        self.wfile.write(f.read().encode('utf-8'))
                except:
                    self.wfile.write(b'{"favorites": [], "archived": [], "hidden": []}')
            else:
                self.wfile.write(b'{"favorites": [], "archived": [], "hidden": []}')
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

        return super().do_GET()

    def do_POST(self):
        if self.path == '/api/interactions':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                req_data = json.loads(post_data.decode('utf-8'))
                
                # Load existing
                data = {"favorites": [], "archived": [], "hidden": []}
                if os.path.exists(INTERACTIONS_FILE):
                    try:
                        with open(INTERACTIONS_FILE, 'r', encoding='utf-8') as f:
                            data = json.load(f)
                    except:
                        pass

                for key in ["favorites", "archived", "hidden"]:
                    if not isinstance(data.get(key), list):
                        data[key] = []
                
                action = req_data.get("action")
                item_id = req_data.get("id") # Using link as ID
                
                if action and item_id:
                    if action == "like":
                        if item_id not in data["favorites"]:
                            data["favorites"].append(item_id)
                        if item_id in data["hidden"]:
                            data["hidden"].remove(item_id)
                        if item_id in data["archived"]:
                            data["archived"].remove(item_id)
                    elif action == "unlike":
                        if item_id in data["favorites"]:
                            data["favorites"].remove(item_id)
                    elif action == "archive":
                        if item_id not in data["archived"]:
                            data["archived"].append(item_id)
                        if item_id in data["favorites"]:
                            data["favorites"].remove(item_id)
                        if item_id in data["hidden"]:
                            data["hidden"].remove(item_id)
                    elif action == "unarchive":
                        if item_id in data["archived"]:
                            data["archived"].remove(item_id)
                    elif action == "restore":
                        if item_id not in data["favorites"]:
                            data["favorites"].append(item_id)
                        if item_id in data["archived"]:
                            data["archived"].remove(item_id)
                        if item_id in data["hidden"]:
                            data["hidden"].remove(item_id)
                    elif action == "hide":
                        if item_id not in data["hidden"]:
                            data["hidden"].append(item_id)
                        if item_id in data["favorites"]:
                            data["favorites"].remove(item_id)
                        if item_id in data["archived"]:
                            data["archived"].remove(item_id)
                    elif action == "unhide":
                        if item_id in data["hidden"]:
                            data["hidden"].remove(item_id)
                
                with open(INTERACTIONS_FILE, 'w', encoding='utf-8') as f:
                    json.dump(data, f, indent=2, ensure_ascii=False)
                    
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(data).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/summarize_favorites':
            print("Received summarize favorites request...")
            try:
                # Load interactions
                favorites = []
                if os.path.exists(INTERACTIONS_FILE):
                    with open(INTERACTIONS_FILE, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        favorites = data.get("favorites", [])
                
                if not favorites:
                    self.send_response(200)
                    self.send_header('Content-type', 'application/json')
                    self.end_headers()
                    self.wfile.write(json.dumps({"status": "ok", "message": "No favorites to summarize."}).encode('utf-8'))
                    return

                result = summarize_specific_papers(favorites)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))
            except Exception as e:
                print(f"Summarize error: {e}")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/preference_report':
            print("Received preference report request...")
            try:
                result = generate_title_report()
                status_code = 200 if result.get("status") == "ok" else 500
                self.send_response(status_code)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps(result).encode('utf-8'))
            except Exception as e:
                print(f"Preference report error: {e}")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/update_abstract':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                req_data = json.loads(post_data.decode('utf-8'))
                item_id = req_data.get("id")
                new_abstract = req_data.get("abstract")
                
                if not item_id or new_abstract is None:
                    raise ValueError("Missing id or abstract")
                
                # Load, Update, Save
                cache = load_abstracts()
                
                # Update logic: preserve existing metadata if possible, but update content
                if item_id not in cache:
                    cache[item_id] = {}
                
                cache[item_id]['abstract'] = new_abstract
                cache[item_id]['source'] = 'user_provided'
                # Also update raw_abstract to ensure future re-summaries use this text
                cache[item_id]['raw_abstract'] = new_abstract 
                cache[item_id]['updated_at'] = datetime.datetime.now().isoformat()
                
                save_abstracts(cache)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "Abstract updated"}).encode('utf-8'))
            except Exception as e:
                print(f"Update abstract error: {e}")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/update_classification':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                req_data = json.loads(post_data.decode('utf-8'))
                item_id = req_data.get("id")
                if not item_id:
                    raise ValueError("Missing id")

                methods = normalize_label_entries(req_data.get("methods", []))
                topics = normalize_label_entries(req_data.get("topics", []))
                theories = [t for t in (req_data.get("theories") or []) if isinstance(t, str)]
                context = [t for t in (req_data.get("context") or []) if isinstance(t, str)]
                subjects = [t for t in (req_data.get("subjects") or []) if isinstance(t, str)]
                novelty_score = req_data.get("novelty_score")

                corrections = load_user_corrections()
                previous = corrections.get(item_id, {})
                correction_count = previous.get("correction_count", 0) + 1

                corrections[item_id] = {
                    "methods": methods,
                    "topics": topics,
                    "theories": theories,
                    "context": context,
                    "subjects": subjects,
                    "novelty_score": novelty_score,
                    "updated_at": datetime.datetime.now().isoformat(),
                    "correction_count": correction_count
                }
                save_user_corrections(corrections)

                primary_method = methods[0]["name"] if methods else "Qualitative"
                primary_topic = topics[0]["name"] if topics else "Other Marketing"
                update_feed_item_classification(item_id, {
                    "methods": methods,
                    "topics": topics,
                    "theories": theories,
                    "context": context,
                    "subjects": subjects,
                    "novelty_score": novelty_score,
                    "method": primary_method,
                    "topic": primary_topic,
                    "classification_source": "user",
                    "user_corrected": True
                })

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "Classification updated"}).encode('utf-8'))
            except Exception as e:
                print(f"Update classification error: {e}")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/categories':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                req_data = json.loads(post_data.decode('utf-8'))
                if not isinstance(req_data, dict):
                    raise ValueError("Invalid categories payload")
                save_categories(req_data)
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "Categories saved"}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/save_config':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            
            try:
                new_config = json.loads(post_data.decode('utf-8'))
                # 读取旧配置以合并（如果有其他字段）
                current_config = {}
                if os.path.exists(CONFIG_FILE):
                    with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                        current_config = json.load(f)
                
                current_config.update(new_config)
                
                # 写入文件
                # 这里的 CONFIG_FILE 是在根目录下，不是 web/ 下，更安全
                with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                    json.dump(current_config, f, ensure_ascii=False, indent=2)
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "Config saved"}).encode('utf-8'))
                
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/journals':
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length)
            try:
                req_data = json.loads(post_data.decode('utf-8'))
                journals = req_data.get("journals", [])
                if not isinstance(journals, list):
                    raise ValueError("Invalid journals payload")
                meta_payload = req_data.get("meta", None)

                cleaned = []
                seen = set()
                for item in journals:
                    if not isinstance(item, str):
                        continue
                    value = item.strip()
                    if value and value not in seen:
                        cleaned.append(value)
                        seen.add(value)

                with open(JOURNALS_FILE, 'w', encoding='utf-8') as f:
                    if cleaned:
                        f.write("\n".join(cleaned) + "\n")
                    else:
                        f.write("")

                meta = {}
                if meta_payload is None:
                    meta = load_journal_meta()
                elif isinstance(meta_payload, dict):
                    for key, value in meta_payload.items():
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

                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({
                    "status": "ok",
                    "journals": cleaned,
                    "meta": cleaned_meta
                }).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/reanalyze':
            print("Received re-analyze request...")
            try:
                from get_RSS import run_reanalysis_flow
                run_reanalysis_flow()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "Re-analysis completed"}).encode('utf-8'))
            except Exception as e:
                print(f"Re-analyze error: {e}")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        if self.path == '/api/fetch':
            print("Received fetch request...")
            try:
                # 运行爬虫和翻译
                run_rss_flow()
                
                self.send_response(200)
                self.send_header('Content-type', 'application/json')
                self.end_headers()
                self.wfile.write(json.dumps({"status": "ok", "message": "Fetch completed"}).encode('utf-8'))
            except Exception as e:
                print(f"Fetch error: {e}")
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"status": "error", "message": str(e)}).encode('utf-8'))
            return

        # 如果不是上述 API，返回 404
        self.send_error(404, "Endpoint not found")
        return

def run_server():
    # 允许地址重用，防止重启时端口被占
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(('', PORT), CustomHandler) as httpd:
        print(f"Server started at http://localhost:{PORT}")
        print("Press Ctrl+C to stop.")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nShutting down server...")
            httpd.shutdown()

if __name__ == "__main__":
    run_server()
