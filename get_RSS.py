import feedparser
import re
import os
import datetime
import time
import json
import hashlib
import requests
from rfeed import Item, Feed, Guid
from email.utils import parsedate_to_datetime
from urllib.parse import urlparse, unquote

# --- 配置区域 ---
OUTPUT_FILE = "filtered_feed.xml"
WEB_DIR = "web"
FEED_JSON = os.path.join(WEB_DIR, "feed.json")
JOURNAL_HASH_FILE = os.path.join(WEB_DIR, "journals.hash")
TRANSLATIONS_CACHE = os.path.join(WEB_DIR, "translations.json")
ABSTRACTS_CACHE = os.path.join(WEB_DIR, "abstracts.json")
CATEGORIES_FILE = os.path.join(WEB_DIR, "categories.json")
USER_CORRECTIONS_FILE = os.path.join(WEB_DIR, "user_corrections.json")
MAX_ITEMS = 1000
ABSTRACT_FETCH_WORKERS = 5
CLASSIFICATION_VERSION = "v2"

# OpenAI 配置
CONFIG_FILE = "config.json"

def get_config():
    config = {
        "OPENAI_API_KEY": os.environ.get("OPENAI_API_KEY"),
        "OPENAI_BASE_URL": os.environ.get("OPENAI_BASE_URL"),
        "OPENAI_PROXY": os.environ.get("OPENAI_PROXY")
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                local_config = json.load(f)
                config.update(local_config)
        except Exception as e:
            print(f"Error reading config file: {e}")
    
    # Clean up empty strings to None
    for key in ["OPENAI_BASE_URL", "OPENAI_PROXY"]:
        if config.get(key) == "":
            config[key] = None
            
    return config

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

def get_abstract_from_crossref(doi):
    """从 Crossref API 获取摘要"""
    if not doi or doi.startswith('pii:'):
        return None

    try:
        url = f"https://api.crossref.org/works/{doi}"
        response = requests.get(url, timeout=10)
        if response.status_code == 200:
            data = response.json()
            abstract = data['message'].get('abstract', '')
            # Crossref 的摘要可能包含 HTML 标签
            if abstract:
                abstract = re.sub(r'<[^>]+>', '', abstract)
                return abstract.strip()
    except Exception as e:
        print(f"Crossref API error for DOI {doi}: {e}")

    return None

def get_abstract_from_semantic_scholar(title):
    """从 Semantic Scholar API 获取摘要和额外信息"""
    if not title:
        return None

    try:
        url = "https://api.semanticscholar.org/graph/v1/paper/search"
        params = {
            'query': title,
            'limit': 1,
            'fields': 'abstract,tldr,citationCount,influentialCitationCount'
        }
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get('data') and len(data['data']) > 0:
                paper = data['data'][0]
                abstract = paper.get('abstract', '')
                # 优先使用 TL;DR（更简洁）
                tldr = paper.get('tldr', {})
                if tldr and tldr.get('text'):
                    return f"{tldr['text']}\n\n{abstract}" if abstract else tldr['text']
                return abstract
    except Exception as e:
        print(f"Semantic Scholar API error for title '{title[:50]}...': {e}")

    return None

def generate_abstract_with_gpt(title, journal, api_key, base_url=None, proxy=None):
    """使用 GPT 基于标题生成研究方向说明"""
    if not title or not api_key:
        return None

    try:
        from openai import OpenAI
        import httpx

        http_client = None
        if proxy:
            http_client = httpx.Client(proxies=proxy)

        client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)

        prompt = f"""Based on the following academic paper title and journal, generate a concise 150-word research summary in Chinese. Describe what the study likely investigates, potential methods, and significance. Keep it academic and objective.

Title: {title}
Journal: {journal}

Provide a structured summary covering: 研究主题、可能的研究方法、预期贡献。"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an academic research assistant. Generate concise, academic-style research summaries in Chinese."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=300,
            temperature=0.7
        )

        return response.choices[0].message.content.strip()

    except Exception as e:
        print(f"GPT abstract generation error: {e}")
        return None

def summarize_abstract_with_gpt(abstract, title, api_key, base_url=None, proxy=None):
    """使用 GPT 基于已有摘要生成中文学术总结"""
    if not abstract or not api_key:
        return None

    try:
        from openai import OpenAI
        import httpx

        http_client = None
        if proxy:
            http_client = httpx.Client(proxies=proxy)

        client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)

        prompt = f"""Summarize the following academic abstract in Chinese (120-150 words). Keep it academic, concise, and objective. Avoid any HTML tags or angle brackets.

Title: {title}
Abstract: {abstract}

Provide a structured summary covering: 研究主题、可能的研究方法、主要贡献。"""

        response = client.chat.completions.create(
            model="gpt-4o-mini",
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
        return None

def fetch_abstract_with_fallback(entry, api_key=None, base_url=None, proxy=None):
    """混合策略获取摘要：Crossref -> Semantic Scholar -> GPT 生成"""
    title = entry.get('title', '')
    link = entry.get('link', '')
    entry_id = entry.get('id', '')
    journal = entry.get('journal', '')

    # 策略 1: 尝试从 Crossref 获取（基于 DOI）
    doi = extract_doi(link, entry_id)
    if doi:
        print(f"  Found DOI: {doi}")
        abstract = get_abstract_from_crossref(doi)
        if abstract and len(abstract) > 100:
            print(f"  [OK] Got abstract from Crossref ({len(abstract)} chars)")
            return abstract, 'crossref', abstract

    # 策略 2: 尝试从 Semantic Scholar 获取（PII 来源跳过以加速）
    if not (doi and doi.startswith('pii:')):
        abstract = get_abstract_from_semantic_scholar(title)
        if abstract and len(abstract) > 100:
            print(f"  [OK] Got abstract from Semantic Scholar ({len(abstract)} chars)")
            return abstract, 'semantic_scholar', abstract

    print(f"  [SKIP] No abstract available")
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
    with open(TRANSLATIONS_CACHE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

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
    with open(ABSTRACTS_CACHE, 'w', encoding='utf-8') as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)

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

def batch_analyze_papers(titles, api_key, base_url=None, proxy=None):
    if not titles or not api_key:
        return {}
    
    from openai import OpenAI
    import httpx
    from concurrent.futures import ThreadPoolExecutor, as_completed
    
    http_client = None
    if proxy:
        print(f"Using proxy: {proxy}")
        http_client = httpx.Client(proxies=proxy)

    client = OpenAI(api_key=api_key, base_url=base_url, http_client=http_client)
    
    analysis_results = {}
    chunk_size = 10
    chunks = [titles[i:i + chunk_size] for i in range(0, len(titles), chunk_size)]
    
    categories = load_categories() or {}
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

    def analyze_chunk(chunk):
        prompt = f"""You are a research classification expert in Business & Marketing.
For each paper title, provide:
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
- Keep the original order of titles in "results".

Example:
{{ "results": [{{ "zh": "示例标题", "methods": [{{"name": "{method_names[0] if method_names else 'Experiment'}", "confidence": 0.8}}], "topics": [{{"name": "{topic_names[0] if topic_names else 'Other Marketing'}", "confidence": 0.7}}], "theories": [], "context": [], "subjects": [], "novelty_score": null }}] }}
"""
        
        user_content = "Titles:\n" + "\n".join([f"{j+1}. {t}" for j, t in enumerate(chunk)])

        try:
            response = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "You are a JSON-only API. You must return valid JSON."},
                    {"role": "user", "content": prompt + "\n\n" + user_content}
                ],
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content
            data = json.loads(content)
            result_list = data.get("results", [])
            
            # Fallback/Validation
            if len(result_list) != len(chunk):
                print(f"Warning: GPT returned {len(result_list)} items for {len(chunk)} titles.")
                return []

            return list(zip(chunk, result_list))
        except Exception as e:
            print(f"Analysis error for chunk: {e}")
            return []

    print(f"Starting concurrent analysis with 20 threads for {len(chunks)} chunks...")
    with ThreadPoolExecutor(max_workers=20) as executor:
        future_to_chunk = {executor.submit(analyze_chunk, chunk): chunk for chunk in chunks}

        completed = 0
        for future in as_completed(future_to_chunk):
            chunk_results = future.result()
            valid_methods = set(method_names)
            valid_topics = set(topic_names)
            for original_title, data in chunk_results:
                methods = normalize_label_entries(data.get("methods", data.get("method", "")), valid_methods)
                topics = normalize_label_entries(data.get("topics", data.get("topic", "")), valid_topics)
                if not methods:
                    methods = [{"name": "Qualitative", "confidence": 0.4}]
                if not topics:
                    topics = [{"name": "Other Marketing", "confidence": 0.4}]
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

def load_config(filename, env_var_name=None):
    """(保持你之前的 load_config 代码不变)"""
    # ... 请保留你之前为了隐私修改过的 load_config 函数 ...
    # 这里为了篇幅省略，请直接复用你现在的 load_config
    if env_var_name and os.environ.get(env_var_name):
        print(f"Loading config from environment variable: {env_var_name}")
        content = os.environ[env_var_name]
        if '\n' in content:
            return [line.strip() for line in content.split('\n') if line.strip()]
        else:
            return [line.strip() for line in content.split(';') if line.strip()]
            
    if os.path.exists(filename):
        print(f"Loading config from local file: {filename}")
        with open(filename, 'r', encoding='utf-8') as f:
            return [line.strip() for line in f if line.strip() and not line.startswith('#')]
            
    return []

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

def parse_rss(rss_url, retries=3):
    # (保持不变)
    print(f"Fetching: {rss_url}...")
    for attempt in range(retries):
        try:
            feed = feedparser.parse(rss_url)
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
            return entries
        except Exception as e:
            print(f"Error parsing {rss_url}: {e}")
            time.sleep(2)
    return []

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
    # (保持不变)
    text_to_search = (entry['title'] + " " + entry.get('summary', '')).lower()
    for query in queries:
        keywords = [k.strip().lower() for k in query.split('AND')]
        match = True
        for keyword in keywords:
            if keyword not in text_to_search:
                match = False
                break
        if match:
            return True
    return False

def generate_rss_xml(items, queries):
    """生成 RSS 2.0 XML 文件 (已加入非法字符清洗)"""
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

    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        f.write(feed.rss())
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

    # 执行分析
    if titles_to_analyze:
        print(f"Analyzing {len(titles_to_analyze)} papers (Translation + Classification)...")
        new_results = batch_analyze_papers(titles_to_analyze, api_key, base_url, proxy)
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

        primary_method = pick_primary(methods, "Qualitative")
        primary_topic = pick_primary(topics, "Other Marketing")

        data.append({
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

    keywords = []
    for query in queries:
        parts = [p.strip() for p in query.split('AND')]
        for part in parts:
            if part:
                keywords.append(part)
    keywords = sorted(set(keywords), key=str.lower)

    payload = {
        "generated_at": datetime.datetime.now().isoformat(),
        "keywords": keywords,
        "items": data
    }

    with open(FEED_JSON, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)
    print(f"Generated {FEED_JSON} with {len(data)} items.")

def compute_journal_hash(journals):
    content = "\n".join(journals).strip() + "\n"
    return hashlib.sha256(content.encode("utf-8")).hexdigest()

def run_rss_flow():
    # 请确保这里的调用参数与你目前的 secrets 配置一致
    rss_urls = load_config('journals.dat', 'RSS_JOURNALS')
    queries = load_config('keywords.dat', 'RSS_KEYWORDS')
    
    if not rss_urls or not queries:
        print("Error: Configuration files are empty or missing.")
        return

    os.makedirs(WEB_DIR, exist_ok=True)
    journal_hash = compute_journal_hash(rss_urls)
    prev_hash = None
    if os.path.exists(JOURNAL_HASH_FILE):
        with open(JOURNAL_HASH_FILE, "r", encoding="utf-8") as f:
            prev_hash = f.read().strip()

    if prev_hash and prev_hash != journal_hash:
        print("Journal list changed. Keeping cached history; only future updates are affected.")

    with open(JOURNAL_HASH_FILE, "w", encoding="utf-8") as f:
        f.write(journal_hash)

    existing_entries = get_existing_items()
    seen_ids = set(entry['id'] for entry in existing_entries)
    
    all_entries = existing_entries.copy()
    new_count = 0

    print("Starting RSS fetch from remote...")
    for url in rss_urls:
        fetched_entries = parse_rss(url)
        for entry in fetched_entries:
            if entry['id'] in seen_ids:
                continue
            
            all_entries.append(entry)
            seen_ids.add(entry['id'])
            new_count += 1
            if match_entry(entry, queries):
                print(f"Keyword match: {entry['title'][:50]}...")

    print(f"Added {new_count} new entries.")
    generate_rss_xml(all_entries, queries)

def run_reanalysis_flow():
    """只运行 AI 分析，不抓取 RSS"""
    print("Starting AI Re-analysis...")
    
    # 1. Load config
    config = get_config()
    api_key = config.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: No API Key configured.")
        return

    # 2. Load existing items from XML (source of truth)
    items = get_existing_items()
    if not items:
        print("No items found to analyze.")
        return

    # 3. Load cache
    translation_cache = load_translations()
    
    # DEBUG: Print sample cache items
    print(f"DEBUG: Cache size: {len(translation_cache)}")
    sample_keys = list(translation_cache.keys())[:3]
    for k in sample_keys:
        print(f"DEBUG SAMPLE: {k[:30]}... -> {translation_cache[k]}")
    
    # 4. Identify items needing analysis
    categories = load_categories() or {}
    VALID_METHODS = [m.get("name") for m in categories.get("methods", []) if isinstance(m, dict) and m.get("name")]
    VALID_TOPICS = [t.get("name") for t in categories.get("topics", []) if isinstance(t, dict) and t.get("name")]
    if not VALID_METHODS:
        VALID_METHODS = ["Experiment", "Archival", "Theoretical", "Review", "Qualitative"]
    if not VALID_TOPICS:
        VALID_TOPICS = ["Other Marketing"]

    titles_to_analyze = []
    for item in items:
        raw_title = item['title']
        cache_val = translation_cache.get(raw_title)

        needs_update = False

        # Case 1: Not in cache
        if not cache_val:
            needs_update = True
        # Case 2: Is old string format
        elif isinstance(cache_val, str):
            needs_update = True
        # Case 3: Is dict but missing keys or has invalid values
        elif isinstance(cache_val, dict):
            cached_methods = normalize_label_entries(cache_val.get("methods", cache_val.get("method", "")), set(VALID_METHODS))
            cached_topics = normalize_label_entries(cache_val.get("topics", cache_val.get("topic", "")), set(VALID_TOPICS))
            if not cached_methods:
                needs_update = True
            if not cached_topics:
                needs_update = True
            if cache_val.get("classification_version") != CLASSIFICATION_VERSION:
                needs_update = True
            
        if needs_update:
            titles_to_analyze.append(raw_title)
            
    if not titles_to_analyze:
        print("All items are already analyzed and up-to-date.")
        # Still need to regenerate JSON to reflect any manual changes
        queries = load_config('keywords.dat', 'RSS_KEYWORDS')
        write_feed_json(items, queries)
        return

    print(f"Found {len(titles_to_analyze)} items needing AI analysis...")
    
    # 5. Run Batch Analysis
    new_results = batch_analyze_papers(titles_to_analyze, api_key, config.get("OPENAI_BASE_URL"), config.get("OPENAI_PROXY"))
    
    # 6. Update Cache
    if new_results:
        translation_cache.update(new_results)
        save_translations(translation_cache)
        print(f"Updated cache with {len(new_results)} new entries.")
    
    # 7. Regenerate JSON
    queries = load_config('keywords.dat', 'RSS_KEYWORDS')
    write_feed_json(items, queries)
    print("Re-analysis complete.")

def summarize_specific_papers(target_ids):
    """
    按需对指定 ID 列表的论文进行 AI 总结。
    target_ids: list of strings (urls/ids)
    """
    print(f"Request to summarize {len(target_ids)} papers...")
    
    # 1. Config
    config = get_config()
    api_key = config.get("OPENAI_API_KEY")
    if not api_key:
        print("Error: No API Key configured.")
        return {"status": "error", "message": "No API Key configured."}
    
    base_url = config.get("OPENAI_BASE_URL")
    proxy = config.get("OPENAI_PROXY")
    
    # 2. Load Items
    items = get_existing_items()
    # Build map for fast access
    item_map = {item['id']: item for item in items}
    
    # 3. Load Cache
    abstract_cache = load_abstracts()
    
    updated_count = 0
    
    for tid in target_ids:
        # Check if item exists in our feed
        if tid not in item_map:
            continue
            
        item = item_map[tid]
        
        # Check if already summarized
        cached = abstract_cache.get(tid, {})
        source = cached.get('source', '')
        
        if source in ['gpt_summarized', 'gpt_generated']:
            continue # Already done
            
        # Do we have raw abstract?
        raw_abstract = cached.get('raw_abstract')
        if not raw_abstract and source in ['crossref', 'semantic_scholar']:
             # If source is external but raw_abstract missing, the 'abstract' field IS the raw one
             raw_abstract = cached.get('abstract')

        # Perform Summary
        summary = None
        new_source = ''
        
        if raw_abstract:
            # Summarize existing
            print(f"Summarizing abstract for: {item['title'][:50]}...")
            summary = summarize_abstract_with_gpt(
                raw_abstract, item['title'], api_key, 
                base_url, proxy
            )
            new_source = 'gpt_summarized'
        else:
            # Generate from scratch
            print(f"Generating summary for: {item['title'][:50]}...")
            summary = generate_abstract_with_gpt(
                item['title'], item['journal'], api_key,
                base_url, proxy
            )
            new_source = 'gpt_generated'
            
        if summary:
            abstract_cache[tid] = {
                'abstract': summary,
                'source': new_source,
                'fetched_at': datetime.datetime.now().isoformat()
            }
            if raw_abstract:
                abstract_cache[tid]['raw_abstract'] = raw_abstract
            updated_count += 1
            
            # Auto-save every 5 updates
            if updated_count % 5 == 0:
                save_abstracts(abstract_cache)

    if updated_count > 0:
        save_abstracts(abstract_cache)
        print("Regenerating feed JSON with new summaries...")
        queries = load_config('keywords.dat', 'RSS_KEYWORDS')
        write_feed_json(items, queries)
        return {"status": "ok", "message": f"Successfully summarized {updated_count} papers."}
    else:
        return {"status": "ok", "message": "No new summaries needed (all up to date)."}

if __name__ == '__main__':
    run_rss_flow()
