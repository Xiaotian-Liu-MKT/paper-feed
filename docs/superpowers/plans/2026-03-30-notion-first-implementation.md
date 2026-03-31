# Paper Feed Notion-First Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor Paper Feed into a Notion-first scheduled pipeline that preserves current RSS/Web outputs while introducing canonical records, Notion upsert, Zotero export, and migration tooling.

**Architecture:** Keep the current batch-driven shape of the project, but insert a new pure canonical layer between RSS fetch and projections. Existing `filtered_feed.xml`, `web/feed.json`, and local server/UI stay alive as read models during the transition, while new Notion/Zotero integrations are added as separate command-style modules and workflows. Machine recovery state lives in a git-backed registry branch; user workflow state moves to Notion.

**Tech Stack:** Python 3.9+, `requests`, `feedparser`, `rfeed`, `openai`, `httpx`, `pytest`, GitHub Actions, Notion REST API, Zotero Web API

---

## Target File Layout

**Create**
- `paper_feed/__init__.py`
- `paper_feed/categories.py`
- `paper_feed/models.py`
- `paper_feed/identity.py`
- `paper_feed/canonical.py`
- `paper_feed/settings.py`
- `paper_feed/registry.py`
- `paper_feed/notion_api.py`
- `paper_feed/notion_schema.py`
- `paper_feed/zotero_api.py`
- `scripts/bootstrap_notion_papers.py`
- `scripts/ingest_to_notion.py`
- `scripts/export_to_zotero.py`
- `tests/conftest.py`
- `tests/test_identity.py`
- `tests/test_canonical.py`
- `tests/test_registry.py`
- `tests/test_notion_schema.py`
- `tests/test_zotero_export.py`
- `requirements-dev.txt`
- `.env.example`

**Modify**
- `get_RSS.py`
- `server.py`
- `requirements.txt`
- `config.json.example`
- `.github/workflows/rss_action.yaml`
- `.gitignore`
- `README.md`

## Implementation Sequence

### Task 1: Build the Foundation Harness

**Files:**
- Create: `paper_feed/__init__.py`
- Create: `tests/conftest.py`
- Create: `tests/test_identity.py`
- Create: `requirements-dev.txt`
- Modify: `requirements.txt`

- [ ] **Step 1: Add a dev/test dependency file**

```txt
# requirements-dev.txt
-r requirements.txt
pytest>=8.0
```

- [ ] **Step 2: Add the initial package marker**

```python
# paper_feed/__init__.py
"""Paper Feed refactor package."""
```

- [ ] **Step 3: Write the first failing identity test**

```python
# tests/test_identity.py
from paper_feed.identity import build_paper_id


def test_build_paper_id_prefers_doi():
    record = {
        "doi": "10.1177/00222429241234567",
        "canonical_url": "https://example.org/paper",
        "title": "A Paper",
        "journal": "Journal of Marketing",
        "published_at": "2026-03-30",
    }

    assert build_paper_id(record) == "doi:10.1177/00222429241234567"
```

- [ ] **Step 4: Add a minimal pytest bootstrap**

```python
# tests/conftest.py
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
```

- [ ] **Step 5: Run the test to confirm it fails**

Run: `pytest tests/test_identity.py -q`

Expected: `ModuleNotFoundError: No module named 'paper_feed.identity'`

- [ ] **Step 6: Add the minimal implementation**

```python
# paper_feed/identity.py
def build_paper_id(record):
    doi = (record.get("doi") or "").strip().lower()
    if doi:
        return f"doi:{doi}"
    raise ValueError("Fallback identity paths not implemented yet")
```

- [ ] **Step 7: Re-run the test**

Run: `pytest tests/test_identity.py -q`

Expected: `1 passed`

- [ ] **Step 8: Commit**

```bash
git add requirements.txt requirements-dev.txt paper_feed/__init__.py paper_feed/identity.py tests/conftest.py tests/test_identity.py
git commit -m "test: add refactor package and initial identity coverage"
```

### Task 2: Extract Canonical Identity and Record Building

**Files:**
- Create: `paper_feed/models.py`
- Create: `paper_feed/categories.py`
- Create: `paper_feed/canonical.py`
- Create: `tests/test_canonical.py`
- Modify: `get_RSS.py`

- [ ] **Step 1: Write failing tests for fallback identity and canonical shaping**

```python
# tests/test_canonical.py
from paper_feed.canonical import build_canonical_record


def test_build_canonical_record_keeps_raw_fields_and_topics():
    item = {
        "id": "https://example.org/item-1",
        "title": "[ScienceDirect Publication] Example Title",
        "link": "https://example.org/item-1",
        "summary": "<p>Publication date: 2026-03-30</p><p>Source: Journal of Marketing</p>",
        "journal": "ScienceDirect Publication: Journal of Marketing",
        "pub_date": "2026-03-30T00:00:00",
    }
    cache = {
        item["title"]: {
            "zh": "示例标题",
            "methods": [{"name": "Experiment", "confidence": 0.9}],
            "topics": [{"name": "AI与营销技术", "confidence": 0.8}],
            "classification_version": "v2",
        }
    }

    record = build_canonical_record(item, cache[item["title"]], abstract_info={})

    assert record.paper_id == "url:0f1b63f5e8d2c86bb0db96c5e6f1f6c60d9af0e5cc5b31d03f5d8f16f9d9f7fb"
    assert record.title == "Example Title"
    assert record.title_zh == "示例标题"
    assert record.method == "Experiment"
    assert record.topics[0]["name"] == "AI与营销技术"
    assert record.journal == "Journal of Marketing"
```

- [ ] **Step 2: Add the canonical dataclass**

```python
# paper_feed/models.py
from dataclasses import dataclass, field


@dataclass
class CanonicalPaperRecord:
    paper_id: str
    source_id: str
    title: str
    title_zh: str
    method: str
    topic: str
    methods: list = field(default_factory=list)
    topics: list = field(default_factory=list)
    link: str = ""
    summary: str = ""
    journal: str = ""
    published_at: str = ""
    doi: str = ""
    raw_abstract: str = ""
    abstract: str = ""
    abstract_source: str = ""
    classification_version: str = ""
```

- [ ] **Step 3: Add shared category helpers**

```python
# paper_feed/categories.py
import json
from pathlib import Path


CATEGORIES_FILE = Path(__file__).resolve().parents[1] / "web" / "categories.json"


def load_categories():
    with CATEGORIES_FILE.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def topic_names():
    return [item["name"] for item in load_categories().get("topics", []) if item.get("name")]


def method_names():
    return [item["name"] for item in load_categories().get("methods", []) if item.get("name")]
```

- [ ] **Step 4: Implement canonical shaping**

```python
# paper_feed/canonical.py
from paper_feed.identity import build_paper_id
from paper_feed.models import CanonicalPaperRecord


def build_canonical_record(item, analysis, abstract_info):
    title = item["title"].replace("[ScienceDirect Publication] ", "").strip()
    journal = item["journal"].replace("ScienceDirect Publication: ", "").strip()
    methods = analysis.get("methods") or []
    topics = analysis.get("topics") or []
    return CanonicalPaperRecord(
        paper_id=build_paper_id(
            {
                "doi": item.get("doi", ""),
                "canonical_url": item.get("link", ""),
                "title": title,
                "journal": journal,
                "published_at": item.get("pub_date", ""),
            }
        ),
        source_id=item["id"],
        title=title,
        title_zh=analysis.get("zh", ""),
        method=(methods[0]["name"] if methods else "Qualitative"),
        topic=(topics[0]["name"] if topics else "Other Marketing"),
        methods=methods,
        topics=topics,
        link=item.get("link", ""),
        summary=item.get("summary", ""),
        journal=journal,
        published_at=item.get("pub_date", ""),
        abstract=abstract_info.get("abstract", ""),
        raw_abstract=abstract_info.get("raw_abstract", ""),
        abstract_source=abstract_info.get("source", ""),
        classification_version=analysis.get("classification_version", ""),
    )
```

- [ ] **Step 5: Replace the `write_feed_json` assembly loop to consume `CanonicalPaperRecord`**

```python
# get_RSS.py (shape of the change)
from paper_feed.canonical import build_canonical_record


record = build_canonical_record(item, cache_data, abstract_info)
data.append(
    {
        "id": record.source_id,
        "paper_id": record.paper_id,
        "title": record.title,
        "title_zh": record.title_zh,
        "method": record.method,
        "topic": record.topic,
        "methods": record.methods,
        "topics": record.topics,
        "link": record.link,
        "summary": record.summary,
        "abstract": record.abstract,
        "raw_abstract": record.raw_abstract,
        "abstract_source": record.abstract_source,
        "journal": record.journal,
        "pub_date": record.published_at,
        "classification_version": record.classification_version,
    }
)
```

- [ ] **Step 6: Run focused tests**

Run: `pytest tests/test_identity.py tests/test_canonical.py -q`

Expected: all tests pass.

- [ ] **Step 7: Run a syntax-only regression check**

Run: `python -m compileall get_RSS.py paper_feed`

Expected: no syntax errors.

- [ ] **Step 8: Commit**

```bash
git add get_RSS.py paper_feed/models.py paper_feed/categories.py paper_feed/canonical.py tests/test_canonical.py
git commit -m "feat: extract canonical paper record builder"
```

### Task 3: Add Refactor Settings and Registry Primitives

**Files:**
- Create: `paper_feed/settings.py`
- Create: `paper_feed/registry.py`
- Create: `tests/test_registry.py`
- Create: `.env.example`
- Modify: `config.json.example`

- [ ] **Step 1: Write failing registry tests**

```python
# tests/test_registry.py
from paper_feed.registry import merge_registry_entry


def test_merge_registry_entry_preserves_foreign_namespace():
    remote = {
        "paper_id": "hash:abc",
        "ingest": {"notion_page_id": "page-1"},
        "export": {"zotero_item_key": "item-1"},
    }
    local = {
        "paper_id": "hash:abc",
        "ingest": {"notion_page_id": "page-2"},
    }

    merged = merge_registry_entry(remote, local, owned_namespace="ingest")

    assert merged["ingest"]["notion_page_id"] == "page-2"
    assert merged["export"]["zotero_item_key"] == "item-1"
```

- [ ] **Step 2: Add environment/config holders**

```python
# paper_feed/settings.py
from dataclasses import dataclass
import os


@dataclass
class IntegrationSettings:
    notion_api_version: str = os.getenv("NOTION_API_VERSION", "2022-06-28")
    notion_parent_page_id: str = os.getenv("NOTION_PARENT_PAGE_ID", "")
    notion_papers_database_id: str = os.getenv("NOTION_PAPERS_DATABASE_ID", "")
    notion_token: str = os.getenv("NOTION_TOKEN", "")
    zotero_api_version: str = os.getenv("ZOTERO_API_VERSION", "3")
    zotero_library_type: str = os.getenv("ZOTERO_LIBRARY_TYPE", "users")
    zotero_library_id: str = os.getenv("ZOTERO_LIBRARY_ID", "")
    zotero_api_key: str = os.getenv("ZOTERO_API_KEY", "")
```

- [ ] **Step 3: Implement the registry merge primitive**

```python
# paper_feed/registry.py
def merge_registry_entry(remote, local, owned_namespace):
    merged = dict(remote)
    merged["paper_id"] = local["paper_id"]
    merged[owned_namespace] = dict(local.get(owned_namespace, {}))
    return merged
```

- [ ] **Step 4: Add config examples**

```json
// config.json.example
{
  "OPENAI_API_KEY": "your-api-key-here",
  "OPENAI_BASE_URL": "",
  "OPENAI_PROXY": "",
  "NOTION_API_VERSION": "2022-06-28",
  "NOTION_PARENT_PAGE_ID": "",
  "NOTION_PAPERS_DATABASE_ID": "",
  "ZOTERO_API_VERSION": "3",
  "ZOTERO_LIBRARY_TYPE": "users",
  "ZOTERO_LIBRARY_ID": "",
  "ZOTERO_API_KEY": ""
}
```

```bash
# .env.example
OPENAI_API_KEY=
NOTION_TOKEN=
NOTION_PARENT_PAGE_ID=
NOTION_PAPERS_DATABASE_ID=
NOTION_API_VERSION=2022-06-28
ZOTERO_API_KEY=
ZOTERO_LIBRARY_TYPE=users
ZOTERO_LIBRARY_ID=
ZOTERO_API_VERSION=3
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_registry.py -q`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add paper_feed/settings.py paper_feed/registry.py tests/test_registry.py config.json.example .env.example
git commit -m "feat: add integration settings and registry primitives"
```

### Task 4: Implement Notion Schema and Bootstrap

**Files:**
- Create: `paper_feed/notion_api.py`
- Create: `paper_feed/notion_schema.py`
- Create: `scripts/bootstrap_notion_papers.py`
- Create: `tests/test_notion_schema.py`

- [ ] **Step 1: Write a failing schema payload test**

```python
# tests/test_notion_schema.py
from paper_feed.notion_schema import build_papers_schema


def test_build_papers_schema_contains_required_properties():
    schema = build_papers_schema()
    props = schema["properties"]
    assert "标题" in props
    assert "状态" in props
    assert "Zotero 状态" in props
    assert "paper_id" in props
    assert "Export Started At" in props
```

- [ ] **Step 2: Implement schema builder**

```python
# paper_feed/notion_schema.py
from paper_feed.categories import topic_names


def build_papers_schema():
    return {
        "title": [{"type": "text", "text": {"content": "Papers"}}],
        "properties": {
            "标题": {"title": {}},
            "状态": {"select": {"options": [{"name": "待看"}, {"name": "收藏"}, {"name": "忽略"}]}},
            "Zotero 状态": {"select": {"options": [{"name": "未导出"}, {"name": "待导出"}, {"name": "导出中"}, {"name": "已导出"}, {"name": "导出失败"}]}},
            "核心话题": {"multi_select": {"options": [{"name": name} for name in topic_names()]}},
            "paper_id": {"rich_text": {}},
            "Export Started At": {"date": {}},
        },
    }
```

- [ ] **Step 3: Implement a thin Notion API client**

```python
# paper_feed/notion_api.py
import requests


class NotionClient:
    def __init__(self, token, api_version):
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Notion-Version": api_version,
                "Content-Type": "application/json",
            }
        )
```

- [ ] **Step 4: Add the bootstrap entrypoint**

```python
# scripts/bootstrap_notion_papers.py
from paper_feed.settings import IntegrationSettings
from paper_feed.notion_schema import build_papers_schema


def main():
    settings = IntegrationSettings()
    if not settings.notion_parent_page_id:
        raise SystemExit("NOTION_PARENT_PAGE_ID is required")
    schema = build_papers_schema()
    print(schema["title"][0]["text"]["content"])


if __name__ == "__main__":
    main()
```

- [ ] **Step 5: Run tests**

Run: `pytest tests/test_notion_schema.py -q`

Expected: pass.

- [ ] **Step 6: Commit**

```bash
git add paper_feed/notion_api.py paper_feed/notion_schema.py scripts/bootstrap_notion_papers.py tests/test_notion_schema.py
git commit -m "feat: add notion schema bootstrap foundation"
```

### Task 5: Implement Dry-Run Notion Ingest

**Files:**
- Create: `scripts/ingest_to_notion.py`
- Modify: `get_RSS.py`
- Modify: `.github/workflows/rss_action.yaml`

- [ ] **Step 1: Write the dry-run behavior first**

```python
# scripts/ingest_to_notion.py
def render_dry_run(records):
    return [
        {
            "paper_id": record.paper_id,
            "title": record.title,
            "status": "待看",
            "zotero_status": "未导出",
        }
        for record in records
    ]
```

- [ ] **Step 2: Wire canonical records into a new command path**

```python
# scripts/ingest_to_notion.py
from get_RSS import get_existing_items, load_translations, load_abstracts
from paper_feed.canonical import build_canonical_record
```

- [ ] **Step 3: Keep current outputs intact**

```python
# get_RSS.py
def run_rss_flow():
    ...
    generate_rss_xml(all_entries, queries)
    return all_entries
```

- [ ] **Step 4: Add a manual workflow entry instead of replacing RSS flow**

```yaml
# .github/workflows/rss_action.yaml (shape of change)
- name: Dry-run Notion ingest
  if: github.event_name == 'workflow_dispatch'
  run: python scripts/ingest_to_notion.py --dry-run
```

- [ ] **Step 5: Verify**

Run:
`python scripts/ingest_to_notion.py --dry-run`

Expected:
prints a JSON or table preview without touching Notion.

- [ ] **Step 6: Commit**

```bash
git add get_RSS.py scripts/ingest_to_notion.py .github/workflows/rss_action.yaml
git commit -m "feat: add dry-run notion ingest command"
```

### Task 6: Implement Zotero Export and Migration Tooling

**Files:**
- Create: `paper_feed/zotero_api.py`
- Create: `scripts/export_to_zotero.py`
- Create: `tests/test_zotero_export.py`
- Modify: `server.py`
- Modify: `README.md`

- [ ] **Step 1: Write a failing item payload test**

```python
# tests/test_zotero_export.py
from paper_feed.zotero_api import build_item_payload


def test_build_item_payload_uses_journal_article():
    payload = build_item_payload(
        title="Example",
        creators=[{"creatorType": "author", "lastName": "Li", "firstName": "Xiaotian"}],
        publication_title="Journal of Marketing",
        url="https://example.org/paper",
        doi="10.1177/00222429241234567",
        paper_id="doi:10.1177/00222429241234567",
    )
    assert payload["itemType"] == "journalArticle"
    assert "pf:id:doi:10.1177/00222429241234567" in payload["tags"][0]["tag"]
```

- [ ] **Step 2: Add the minimal Zotero payload builder**

```python
# paper_feed/zotero_api.py
def build_item_payload(title, creators, publication_title, url, doi, paper_id):
    return {
        "itemType": "journalArticle",
        "title": title,
        "creators": creators,
        "publicationTitle": publication_title,
        "url": url,
        "DOI": doi,
        "tags": [{"tag": f"pf:id:{paper_id}"}],
    }
```

- [ ] **Step 3: Add a placeholder export command that consumes Notion-ready rows**

```python
# scripts/export_to_zotero.py
def main():
    raise SystemExit("Export implementation starts after Notion ingest dry-run is stable")
```

- [ ] **Step 4: Begin local API deprecation notes instead of breaking the UI**

```python
# server.py
# Keep existing /api/interactions live in phase 1, but add comments and logging
# marking it as legacy-local state during transition.
```

- [ ] **Step 5: Update documentation**

```markdown
# README.md
- Add a section for `requirements-dev.txt`
- Document `scripts/bootstrap_notion_papers.py`
- Document `scripts/ingest_to_notion.py --dry-run`
- Mark local interactions as legacy transition state
```

- [ ] **Step 6: Commit**

```bash
git add paper_feed/zotero_api.py scripts/export_to_zotero.py tests/test_zotero_export.py server.py README.md
git commit -m "feat: add zotero export foundation and transition docs"
```

## Subagent Execution Guidance

Use subagents after Task 1 stabilizes the test harness.

- `Schema owner` can take Task 4.
- `Ingest owner` can take Task 5.
- `Zotero owner` can take Task 6.
- `Migration owner` should start only after Task 3 lands.

Keep Task 2 local or under a single high-context implementer. It is the seam everything else depends on.

## Spec Coverage Check

- Canonical `paper_id`, taxonomy reuse, and batch-driven architecture are covered by Tasks 2-5.
- Notion schema/bootstrap and config discipline are covered by Tasks 3-4.
- Zotero export and phase-1 transition semantics are covered by Task 6.
- Legacy UI preservation is handled by keeping `server.py` and existing projections alive until later cutover.

## Immediate Execution Order

Start now with:

1. Task 1
2. Task 2
3. Task 3

Those three tasks create a safe base for subagent parallelism on Notion and Zotero.
