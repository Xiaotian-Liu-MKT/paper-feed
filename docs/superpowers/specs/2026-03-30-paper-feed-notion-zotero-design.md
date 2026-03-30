# Paper Feed Notion-First Refactor Design

Date: 2026-03-30
Status: Draft approved in conversation, written for implementation planning
Scope: Medium-to-heavy refactor with Notion as user-facing source of truth, scheduled GitHub Actions execution, and Zotero export integration

## 1. Goal

Refactor Paper Feed from a local file-first RSS viewer into a Notion-first paper inbox:

- New papers are automatically ingested into a Notion `Papers` database.
- The user triages papers primarily in Notion.
- A Notion button marks selected favorites for Zotero export.
- A scheduled background job exports those papers to Zotero and writes the result back into Notion.

This design intentionally avoids an always-on backend in phase 1.

## 2. Product Decisions

Confirmed decisions:

- Notion is the only source of truth for user-facing workflow state.
- AI deep summary is out of scope for this project.
- Daily or periodic automatic ingest into Notion Inbox is required.
- Zotero export must be triggerable from a Notion button.
- Export does not need real-time execution; scheduled polling is acceptable.
- Existing web UI may remain as a secondary read model or be retired later.

Rejected for phase 1:

- Real-time webhook-triggered backend
- Notion-native execution as the critical automation engine
- WhatsApp or messaging-driven workflow
- Zotero RIS/BibTeX as the primary sync path

## 3. Architecture Summary

Phase 1 target architecture:

1. RSS ingest job fetches configured feeds.
2. Python normalization/dedup pipeline converts items into canonical paper records.
3. Lightweight AI classification enriches title translation, method, and topic.
4. Canonical records are upserted into Notion `Papers`.
5. User triages papers in Notion with `待看 / 收藏 / 忽略`.
6. A Notion button changes `Zotero 状态` to `待导出`.
7. A separate scheduled GitHub Actions job polls Notion for exportable papers.
8. Export job writes metadata to Zotero through the official Web API.
9. Export result is written back to Notion.

System layers:

- Source ingest
- Normalization and identity
- AI enrichment
- Notion materialization
- Scheduled export execution
- Derived projections for legacy UI and audits

## 4. Source of Truth Model

The system distinguishes between user truth and machine recovery state.

### 4.1 User Truth

Notion is the sole user-facing source of truth for:

- reading workflow state
- export request state
- manual overrides
- notes
- paper-level review decisions

### 4.2 Machine Recovery State

Notion should not be the only machine-recovery primitive. Keep a thin shadow registry outside Notion for:

- `paper_id`
- `notion_page_id`
- `upstream_fingerprint`
- last successful sync timestamps
- Zotero export checkpoints or result summaries

This is not a second user-facing source of truth. It exists to support:

- idempotent upserts
- dedupe without relying on Notion search
- replay after failures
- audits and repair

Single durable machine-recovery store in phase 1:

- dedicated `automation-state` branch containing `state/registry.json`

Registry shape:

- one map keyed by `paper_id`
- each entry stores only latest known machine state:
  - `paper_id`
  - `ingest` namespace:
    - `notion_page_id`
    - `upstream_fingerprint`
    - `last_seen_at`
  - `export` namespace:
    - `zotero_item_key` if any
    - `last_export_hash` if any
    - `last_export_attempt_at` if any
    - `last_export_terminal_state` if any

Locking and update model:

- only GitHub Actions writes this store
- both workflows read the latest `automation-state` branch before execution
- workflow `concurrency` prevents same-workflow overlap
- writes are full-file rewrites after successful batch completion
- failed runs must not partially rewrite the registry
- `automation-state` branch must not trigger product workflows
- workflows push state commits only to `automation-state`
- push protocol is `fetch -> rebase -> rewrite -> commit -> push`, with bounded retry on non-fast-forward failure
- on non-fast-forward retry, workflow must merge at `paper_id` key level and preserve the namespace owned by the other workflow
- ingest workflow may only mutate `ingest` namespace
- export workflow may only mutate `export` namespace

This store is required. Phase 1 must not rely on ephemeral local files or Actions artifacts as the only recovery mechanism.

Crash-recovery rule for partial export success:

- export workflow must set Notion `Zotero 状态=导出中` before any Zotero API write
- if Zotero write succeeds but final Notion write-back fails, next export run must query stale `导出中` items and reconcile by matching Zotero machine tags (`pf:id:<paper_id>`)
- recovery for this case must not depend on an in-memory queue or a partially written state file

Compaction and retention:

- `state/registry.json` is not append-only
- each `paper_id` stores only the latest machine state
- weekly compaction removes registry entries for papers not seen upstream for 180 days and with no `zotero_item_key`
- exported papers keep only minimal terminal export metadata, not per-run history

## 5. Canonical Record Contract

Every paper must be normalized into a canonical record before touching Notion.

Required fields:

- `paper_id`
- `title`
- `title_zh`
- `method`
- `topics`
- `published_at`
- `journal`
- `source`
- `canonical_url`
- `doi`
- `authors`
- `raw_abstract` if available
- `upstream_fingerprint`
- `ingested_at`

### 5.1 Identity Rules

`paper_id` generation priority:

1. DOI
2. normalized canonical URL
3. stable hash of normalized `title + journal + published_date`

Rules:

- `paper_id` is the canonical machine identifier.
- `notion_page_id` is a downstream location identifier, not a business key.
- Current mixed usage of `id`, `link`, and raw title must be retired.

Normalization rules:

- DOI normalization:
  - trim whitespace
  - lowercase
  - strip `https://doi.org/` or `http://doi.org/` prefix
  - URL-decode once
  - strip trailing punctuation
- URL normalization:
  - lowercase host only
  - remove fragment
  - remove trailing slash
  - drop known tracking query parameters
  - sort remaining query parameters for stable serialization
- Title normalization for hash fallback:
  - strip HTML
  - Unicode NFKC normalization
  - lowercase
  - remove punctuation
  - collapse whitespace
- Journal normalization:
  - reuse existing cleaned journal-title logic after extraction into shared utilities
- Date normalization:
  - prefer `YYYY-MM-DD`
  - fallback to `YYYY-MM`
  - fallback to `YYYY`

Hash specification for fallback identity:

- `paper_id = sha256(title_norm + "|" + journal_norm + "|" + date_norm)`

## 6. Notion Database Design

Use one primary data source called `Papers`.

### 6.1 Properties

- `标题` (`title`)
- `状态` (`status`): `待看`, `收藏`, `忽略`
- `Zotero 状态` (`status`): `未导出`, `待导出`, `导出中`, `已导出`, `导出失败`
- `标题中文` (`rich_text`)
- `研究方法` (`select`)
- `核心话题` (`multi_select`)
- `发布日期` (`date`)
- `期刊` (`rich_text`)
- `来源` (`rich_text`)
- `Canonical URL` (`url`)
- `DOI` (`rich_text`)
- `paper_id` (`rich_text`)
- `Upstream Fingerprint` (`rich_text`)
- `Ingested At` (`date`)
- `Last Synced At` (`date`)
- `Zotero Item Key` (`rich_text`)
- `Exported At` (`date`)
- `Export Error` (`rich_text`)
- `人工锁定` (`checkbox`)

Optional fields:

- `作者摘要` (`rich_text`) if a short authors string is useful for views
- `分类版本` (`rich_text`)
- `来源类型` (`select`) if later needed for source-specific handling

### 6.2 Page Body

Long text should live in the page body, not properties:

- system raw abstract
- user notes
- audit annotations

Page body block ownership is fixed:

- ingest workflow owns `SYSTEM_RAW_ABSTRACT`
- export workflow owns `SYSTEM_AUDIT`
- user owns `USER_NOTES`

Jobs may only rewrite their own anchored block sections. Jobs must not rewrite or reorder blocks owned by another actor.

### 6.3 Property Ownership Matrix

Ingest workflow owns:

- `标题`
- `标题中文`
- `研究方法`
- `核心话题`
- `发布日期`
- `期刊`
- `来源`
- `Canonical URL`
- `DOI`
- `paper_id`
- `Upstream Fingerprint`
- `Ingested At`
- `Last Synced At`

Export workflow owns:

- `Zotero 状态`
- `Zotero Item Key`
- `Exported At`
- `Export Error`

User owns:

- `状态`
- `人工锁定`
- page-body `USER_NOTES`

Lock behavior:

- if `人工锁定=true`, ingest workflow must not overwrite descriptive user-visible fields
- in locked state, ingest may only update `Upstream Fingerprint` and `Last Synced At`
- if upstream canonical metadata changed while locked, ingest writes an audit note instead of overwriting display fields

## 7. State Machines

Reading state and export state are separate.

### 7.1 Reading State

Allowed values:

- `待看`
- `收藏`
- `忽略`

Allowed transitions:

- `待看 -> 收藏`
- `待看 -> 忽略`
- `收藏 -> 待看`
- `收藏 -> 忽略`
- `忽略 -> 待看`

### 7.2 Zotero State

Allowed values:

- `未导出`
- `待导出`
- `导出中`
- `已导出`
- `导出失败`

Rules:

- only papers with `状态=收藏` can move to `待导出`
- Notion button changes only export state
- successful Zotero write plus successful Notion write-back is required before `已导出`
- failed or ambiguous exports move to `导出失败`
- enforcement belongs to the export workflow, not the Notion button itself

Transition flow:

- new item: `未导出`
- button click: `待导出`
- export job starts: `导出中`
- success: `已导出`
- failure: `导出失败`
- retry button or manual change: `导出失败 -> 待导出`

## 8. Notion Button Behavior

The Notion button is a UX trigger, not an external execution engine.

Buttons:

- `加入 Zotero`: set `Zotero 状态=待导出`
- `重试导出`: set `Zotero 状态=待导出` from failure state

Phase 1 deliberately does not rely on native Notion webhook actions as the primary engine. The actual work is executed by scheduled polling jobs.

Invalid transition handling:

- if export workflow sees `Zotero 状态=待导出` but `状态!=收藏`, it must:
  - set `Zotero 状态=未导出`
  - set `Export Error=Rejected: 状态 must be 收藏 before export`
  - skip Zotero API calls for that page

## 9. GitHub Actions Design

Use two separate workflows.

### 9.1 `ingest-to-notion`

Purpose:

- fetch RSS
- normalize and dedupe
- run lightweight AI classification
- upsert into Notion
- refresh optional read models

Schedule:

- daily or every 6 hours

Responsibilities:

- must be idempotent
- must respect rate limits and retries
- must update machine-managed fields only
- must avoid overwriting user-controlled fields
- must upsert against `paper_id` using `state/registry.json`
- must not depend on Notion search for identity resolution

Query completeness protocol:

- the ingest job does not use time-based Notion polling
- for each canonical record, resolve `paper_id` through `state/registry.json`
- if registry entry is missing, query the `Papers` data source by exact `paper_id` property filter, not global search
- after a successful create/update, rewrite `state/registry.json`

### 9.2 `export-zotero`

Purpose:

- query Notion for `状态=收藏` and `Zotero 状态=待导出`
- export to Zotero
- write back export result

Schedule:

- every 15 to 30 minutes

Responsibilities:

- transition `待导出 -> 导出中 -> 已导出 or 导出失败`
- handle retries safely
- write back `Zotero Item Key`
- fully exhaust the filtered Notion result set every run

Query completeness protocol:

- exporter queries Notion by property filters, never by search
- exporter must paginate to exhaustion on every run using `start_cursor`
- no time-based watermark is allowed for pending export discovery
- ordering should be deterministic, using `last_edited_time` ascending when available
- items left in `导出中` for longer than one polling interval must be re-evaluated on the next run
- exporter must reconcile stale `导出中` items before processing fresh `待导出` items

### 9.3 Workflow Hardening

Both workflows must include:

- non-top-of-hour cron timing
- `concurrency` protection
- retry with backoff for rate limits and transient HTTP failures
- manual dispatch support
- clear logs and failure notifications

## 10. Zotero Integration Design

Primary integration path: Zotero Web API.

Fallback path: manual RIS/BibTeX export only for exceptional recovery scenarios.

### 10.1 Field Mapping

- `标题 -> title`
- `作者 -> creators`
- `发布日期 -> date`
- `期刊 -> publicationTitle`
- `DOI -> DOI`
- `Canonical URL -> url`
- `研究方法 / 核心话题 -> tags`
- `Notion metadata -> note or extra`

Do not write machine-generated summaries into Zotero `abstractNote` unless they are verified source-backed abstracts.

### 10.2 Dedup and Idempotency

Dedup order:

1. existing `Zotero Item Key` in Notion
2. machine tag like `pf:id:<paper_id>`
3. DOI match
4. normalized title plus first author plus year match
5. otherwise fail safely

Required protocol:

- record returned `itemKey`
- record version headers when relevant
- handle stale writes and conflict responses
- never assume create-only semantics
- on successful Zotero write plus failed Notion write-back, leave page in `导出中` and rely on the next run's stale-item reconciliation using Zotero machine tags
- every Zotero create, update, and reconciliation path must ensure machine tag `pf:id:<paper_id>` is present
- if a previously exported Zotero item lacks that machine tag, export workflow must repair the tag before considering recovery complete

## 11. Legacy UI and Projections

`feed.json` and `filtered_feed.xml` become projections, not canonical stores.

Phase 1 options:

- keep the current web UI as a read-only projection
- keep preference report generation if rebuilt from the new projection
- remove local interactive state writes at cutover

Explicit non-goal for phase 1:

- preserving `interactions.json` as a canonical state container

Cutover shutdown policy:

- disable write endpoints that mutate workflow truth, including local interaction writes and local classification/state edits tied to canonical workflow state
- only read-only projection endpoints remain active after cutover
- cutover sign-off requires explicit user approval after dry-run and sample validation

## 12. Migration Plan

Migration order is fixed to reduce risk.

1. Freeze canonical contract.
2. Freeze Notion schema.
3. Extract reusable normalization and identity utilities from current monoliths.
4. Add Notion upsert path without removing current outputs.
5. Add shadow registry.
6. Run dry-run backfill on 50 to 100 records.
7. Validate duplicate handling and identity parity.
8. Enable scheduled `RSS -> Notion` ingest.
9. Enable `Notion -> Zotero` export.
10. Downgrade old web app to projection mode.
11. Remove assumptions that `feed.json` or `interactions.json` is canonical.
12. Disable legacy write endpoints.

### 12.1 Historical Data Migration

Special care is required because current state uses mixed identifiers:

- UI interactions mostly use `link`
- abstracts and classification flows use `id`
- some caches use raw title

Migration script must:

- resolve legacy link-based interactions against canonical `paper_id`
- flag collisions and unresolved records
- never silently merge ambiguous items

Disposition flow for unresolved records:

- `auto-resolved`
- `manual-review-required`
- `skipped-preserve-legacy`

Operational owner:

- migration owner prepares collision report
- main agent reviews collision report
- user approves final disposition before cutover

## 13. Risks and Failure Modes

Must be explicit in the design:

- Notion API rate limits and payload constraints
- Notion search is not exhaustive or immediately consistent
- GitHub Actions schedules can be delayed or dropped
- scheduled workflows on public repos may be auto-disabled after inactivity
- identity split in the current codebase
- schema drift from manual Notion edits
- secret concentration in GitHub Actions
- no always-on service means no real queue or dead-letter system
- vendor lock-in if all workflow logic depends on Notion views and fields

## 14. Risk Mitigations

- never use Notion search as the primary identity layer
- use `paper_id` plus shadow registry for machine matching
- keep a thin local admin CLI or repair script set
- run schema validation before each batch workflow
- separate ingest and export workflows completely
- preserve manual replay paths for both workflows
- keep derived read models available during cutover
- require exact-property database queries and full pagination, never Notion search, for critical machine matching
- use a dedicated `automation-state` branch for machine registry writes

## 15. Security Requirements

Minimum phase 1 requirements:

- workflows containing Notion, Zotero, or OpenAI secrets must run only from a private repo or a private deployment mirror
- Notion integration token must be scoped only to the target workspace/database access required
- Zotero credentials must be limited to the intended personal library context
- OpenAI secret is available only to ingest workflow, not export workflow
- secrets must be separated by workflow instead of one shared global environment
- workflow logs must never print tokens, raw auth headers, or full sensitive payloads
- secret rotation cadence must be documented during implementation planning

## 16. Subagent Strategy For Implementation

Implementation should use subagents by data-plane seam, not by framework layer.

### 16.1 Main Agent Ownership

Main agent owns:

- canonical contract
- identity rules
- field precedence
- migration order
- cutover decisions
- review of all cross-boundary changes

### 16.2 Suggested Subagent Roles

- `Schema owner`
  - Notion schema
  - state machine
  - field docs
- `Ingest owner`
  - RSS parsing
  - normalization
  - dedupe
  - Notion upsert path
- `Zotero owner`
  - export protocol
  - idempotency
  - state write-back
- `Migration owner`
  - backfill
  - audits
  - validation scripts
- `Projection/UI owner`
  - read model
  - legacy UI downgrade

### 16.3 Review Checkpoints

1. schema freeze review
2. sample migration review
3. `RSS -> Notion` end-to-end review
4. `Notion -> Zotero` end-to-end review
5. cutover review
6. post-cutover audit

### 16.4 What Must Not Be Parallelized Early

Until the canonical data contract is frozen, avoid parallel code changes in:

- `get_RSS.py`
- `server.py`
- `web/app.js`

These files currently contain the most coupling and identity assumptions.

## 17. Why This Is Medium vs Heavy

This remains a medium refactor if phase 1 only:

- moves user truth to Notion
- adds scheduled Notion ingest and scheduled Zotero export
- preserves a thin projection path for the old UI
- avoids an always-on backend

It becomes heavy if later phases add:

- real-time webhook execution
- independent operational database
- full dual-write or dual-UI support
- complex retry consoles and dashboards
- advanced sync in both directions

## 18. Acceptance Criteria For Phase 1

Phase 1 is complete when:

- new RSS papers appear automatically in Notion
- user can triage papers in Notion
- user can press a Notion button to request Zotero export
- scheduled export job successfully exports and writes back status
- duplicate creation is controlled by `paper_id`
- the system remains recoverable without relying on Notion search
- legacy local files are no longer treated as canonical workflow state
- all legacy write endpoints are disabled after cutover

Quantitative gates:

- dry-run migration sample of 100 records produces 0 silent merges
- dry-run migration sample has 100% parity on required canonical fields
- sample export run of 20 valid `待导出` items completes with 100% deterministic terminal states (`已导出` or `导出失败`)
- replay from `state/registry.json` can rebuild Notion page mapping for the sample without using Notion search
