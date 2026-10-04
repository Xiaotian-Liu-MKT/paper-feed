# Journal Dashboard Design Log
Date: 2026-01-11
Scope: Journal "quality & fit" + "structure comparison" merged plan

## Goal
Design a single "Journal Fit Profile" feature that separates:
- Behavioral Fit: based on favorites/hidden/neutral
- Structure Fit: topic/method distribution + comparison

This document tracks decisions, definitions, and UI plan so we do not miss steps.

## Data Sources (Existing)
- `web/feed.json` items: `journal`, `topics`/`topic`, `methods`/`method`, `pub_date`, `link`
- `web/interactions.json`: `favorites`, `hidden`
- `web/categories.json`: topic/method taxonomy + keywords
- `server.py` preference report: already computes lift for method/topic (optional reuse)

## Definitions
### 1) Behavioral Fit
Per journal:
- `total = liked + disliked + neutral`
- `like_rate = liked / total`
- `dislike_rate = disliked / total`
- `fit_score = (like_rate - dislike_rate) * 100`  (range -100..100)
- `confidence = log(total+1) / log(50+1)`  (cap at 1.0)

Notes:
- Display warnings when `total < 10` ("样本不足") or `confidence < 0.4`.
- Neutral = items without favorite/hidden.

### 2) Structure Fit
Per journal:
- Topic distribution = normalized counts of `topics` / `topic`
- Method distribution = normalized counts of `methods` / `method`

Comparison modes:
1) Journal vs My Favorites baseline
2) Journal vs Another Journal

Similarity:
- Use cosine similarity for topic and method vectors.
- `structure_score = 0.7 * topic_sim + 0.3 * method_sim`

## Research Abstract Match
Input: project abstract or short description.
Extraction (local keyword match):
- Use `categories.json` keywords to build a query topic/method vector.
- Match against journal distributions.

Ranking:
- `final_score = structure_score * confidence`
- Output top 10 journals with explanation:
  - Top matched topics/methods keywords
  - Journal fit score (behavioral)

## UI Plan (stats page)
Add a new tab called "期刊匹配画像" inside `web/stats.html`.

### Layout structure
Section A: Behavioral Fit
- Card: "期刊匹配度"
  - Big score (fit_score), color scale
  - Confidence badge (high/medium/low)
  - Sample size note (total, liked, disliked, neutral)
  - Mini ratio bar (reuse existing ratio bar styles)
- Helper text: explain formula + sample caveat

Section B: Structure Comparison
- Controls:
  - Compare mode: [Journal vs Favorites] / [Journal vs Journal]
  - Secondary journal selector (only when in J vs J mode)
- Topic Distribution:
  - Two stacked bars (left: current journal, right: compare baseline)
  - Top-5 topic labels with % and delta
- Method Distribution:
  - Two stacked bars (left: current journal, right: compare baseline)
  - Top-3 method labels with % and delta
- Similarity panel:
  - Topic similarity
  - Method similarity
  - Overall structure score

Section C: Project-to-Journal Matcher
- Textarea input (project abstract / idea)
- "Match Journals" button
- Output list:
  - Journal name + score
  - Matched topic/method keywords
  - Behavior fit score + confidence
  - Quick action: "筛选" link to index.html?journal=

## Open Questions
- Where to place: new tab in `web/stats.html` or new page?
- Should "baseline" use all favorites or favorites + neutral?
- Should "method" comparison be optional if missing data?

## Next Steps
1) Confirm UI location and baseline definitions.
2) Implement stats JS aggregations and matching.
3) Update styles for new panels.

## UI Wireflow (Elements + IDs + Data Bindings)
Location: `web/stats.html` new tab panel "fit".

### Tab bar
- Button: `data-tab="fit"` label "期刊匹配画像"
- Panel: `data-tab-panel="fit"`

### Section A: Behavioral Fit
- Wrapper: `section.panel` id `fitBehaviorPanel`
- Title: "期刊匹配度"
- Score:
  - `div#fitScoreValue` (number, -100..100)
  - `div#fitScoreLabel` (e.g. "高匹配" / "中性" / "低匹配")
- Confidence:
  - `span#fitConfidenceBadge` (High/Medium/Low)
  - `span#fitSampleNote` (e.g. "样本 42 篇")
- Ratio bar (reuse existing CSS):
  - `div#fitRatioLike` (width = like_rate)
  - `div#fitRatioDislike` (width = dislike_rate)
  - `div#fitRatioNeutral` (width = neutral_rate)
- Breakdown text:
  - `div#fitBreakdownText` ("喜欢 12 / 不喜欢 5 / 未反馈 25")
- Hint:
  - `p#fitBehaviorHint` (formula + sample caveat)

Bindings:
- Inputs: current journal selection + date range + interactions
- Computed: total, liked, disliked, neutral, rates, fit_score, confidence

### Section B: Structure Comparison
- Wrapper: `section.panel` id `fitStructurePanel`
- Controls:
  - Select `select#fitCompareMode` values: `favorites`, `journal`
  - Secondary journal select `select#fitCompareJournal` (visible only if mode=journal)
- Topic distribution:
  - `div#fitTopicBars` container
  - Each bar row:
    - `div.fit-bar-row` with `data-series="current|baseline"`
    - `div.fit-bar-label`
    - `div.fit-bar-track` with children `.fit-bar-seg` (topic %, color)
  - Top topics list: `div#fitTopicTopList` (5 items)
- Method distribution:
  - `div#fitMethodBars`
  - Top methods list: `div#fitMethodTopList` (3 items)
- Similarity panel:
  - `div#fitTopicSim` (0..1)
  - `div#fitMethodSim` (0..1)
  - `div#fitStructureScore` (0..100)

Bindings:
- Current journal vectors (topic/method)
- Baseline vectors:
  - mode=favorites: aggregate from favorited items
  - mode=journal: aggregate from selected comparison journal
- Similarity: cosine(topic), cosine(method)
- Overall: 0.7*topic + 0.3*method

### Section C: Project-to-Journal Matcher
- Wrapper: `section.panel` id `fitMatcherPanel`
- Input:
  - `textarea#fitAbstractInput` (placeholder: project abstract)
  - `button#fitMatchBtn` label "匹配期刊"
  - `div#fitMatchStatus` (loading / empty)
- Results:
  - `div#fitMatchResults`
  - Result item:
    - `div.fit-match-title` (journal name)
    - `div.fit-match-score` (final score)
    - `div.fit-match-explain` (matched topics/methods)
    - `a.fit-match-filter` href `index.html?journal=...`

Bindings:
- On click: parse keywords using `categories.json`
- Build query vectors (topic/method)
- Compare with each journal's structure vectors
- Combine with behavioral confidence and fit_score for ranking

### Events Summary
- `statsJournal` / date filters change -> recompute A + B panels
- `fitCompareMode` change -> show/hide secondary select + recompute
- `fitCompareJournal` change -> recompute B panel
- `fitMatchBtn` click -> run matcher, render results

## Implementation Log
Date: 2026-01-11
Completed:
- Added new tab "期刊匹配画像" with three panels (behavior fit, structure comparison, project matcher).
- Implemented fit calculations, cosine similarity, and keyword-based matcher.
- Wired new controls to existing date range + journal selector.
- Added UI styles for bars, scores, and match results.

Files updated:
- `web/stats.html`
- `web/stats.js`
- `web/styles.css`

## Update Log
Date: 2026-01-11
Change:
- Switched "期刊匹配画像" from single-journal view to full journal overview.
- Added overview list with fit score, confidence, structure match, and Topic/Method bars per journal.
- Baseline panel now shows comparison reference only (favorites or chosen journal).
