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
- Zotero export must be triggerable from a Notion property change; native button support is an optional convenience layer when available.
- Export does not need real-time execution; scheduled polling is acceptable.
- Existing web UI may remain as a secondary read model or be retired later.
- Secret-bearing automation may run only from a private repo or a private automation mirror.
- Phase 1 pins `Notion-Version: 2022-06-28` for all API requests.
- If native Notion button automation is unavailable, the supported fallback is manual property edit to `Zotero 状态=待导出`.
- Phase 1 uses a private automation mirror for workflow execution; the current repo may remain public for source hosting if desired.

Pre-condition for phase 1 automation:

- before any Notion, Zotero, or OpenAI secret is added to GitHub Actions, the private automation mirror must exist and own the scheduled workflows
- if the current repository is later made private, the mirror may be retired after explicit migration

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

Phase 1 `automation-state` layout:

- `state/registry.json`
- `state/heartbeat.json`
- `state/duplicate_audit/`
- `state/legacy_unresolved.json`
- `state/legacy_unresolved.csv`
- optional quarterly compatibility reports under `state/compatibility/`

Dry-run state branch:

- `automation-state-dry-run` mirrors the same directory layout as `automation-state`
- `automation-state-dry-run` is isolated from production workflows and may be deleted after dry-run sign-off
- branch protection for `automation-state-dry-run` should mirror `automation-state`

Registry shape:

- top-level `metadata` object:
  - `schema_version`
  - `last_compaction_week` in `YYYY-Www` format
- top-level `papers` map keyed by `paper_id`
- each paper entry stores only latest known machine state:
  - `paper_id`
  - `ingest` namespace:
    - `notion_page_id`
    - `upstream_fingerprint`
    - `last_seen_at`
    - `identity_aliases`
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
- if `automation-state` branch or `state/registry.json` does not yet exist, that is treated as a valid first-run bootstrap state equivalent to:
  - `metadata.schema_version=1`
  - `metadata.last_compaction_week=""`
  - `papers={}`
- `automation-state` branch must not trigger product workflows
- workflows push state commits only to `automation-state`
- registry merge, retry, and compaction logic must live in a dedicated tested utility module rather than ad-hoc shell snippets
- push protocol is `fetch -> rebase -> rewrite -> commit -> push`, with at most 5 retries on non-fast-forward failure
- retry backoff is `5s, 10s, 20s, 40s, 60s`
- on non-fast-forward retry, workflow must merge at `papers.<paper_id>` key level and preserve the namespace owned by the other workflow
- retry path must explicitly re-read remote `state/registry.json`, parse it as structured JSON, merge only owned namespaces at `papers.<paper_id>` key level, and then rewrite the merged full file
- retry path metadata handling is:
  - export workflow preserves the remote top-level `metadata` object unchanged
  - ingest workflow preserves all remote top-level `metadata` keys except `last_compaction_week`, which it may update when it owns a completed compaction write
- ingest workflow may only mutate `ingest` namespace
- export workflow may only mutate `export` namespace
- ingest workflow owns `metadata.last_compaction_week`
- export workflow must preserve the remote `metadata` object unchanged on every registry rewrite
- if all push retries fail, workflow must fail hard, leave remote product state unchanged from the last successful remote write, and rely on next-run reconciliation from Notion exact-property queries and Zotero machine-tag recovery
- weekly compaction is owned by the ingest workflow and runs on the first successful ingest run of each ISO week
- if `metadata.last_compaction_week` is more than 2 ISO weeks behind, the next successful ingest run must execute one catch-up compaction before resuming the normal weekly cadence
- catch-up or weekly compaction must run only after the current ingest batch's Notion upserts and registry write have completed successfully
- export retry merge must not recreate a compacted entry that is absent from the remote registry unless the current export run has just produced or confirmed a valid `zotero_item_key` for that same `paper_id`

This store is required. Phase 1 must not rely on ephemeral local files or Actions artifacts as the only recovery mechanism.

Registry corruption recovery procedure:

- dedicated admin workflow: `rebuild_registry`
- owner: main agent or designated operator through `workflow_dispatch` in the private automation mirror
- inputs:
  - Notion pages queried by exact `paper_id` property pagination
  - Zotero items scanned by machine tag `pf:id:<paper_id>`
- output:
  - full regenerated `state/registry.json`
- failure behavior:
  - workflows abort if registry JSON is malformed
  - operator runs `rebuild_registry` before re-enabling workflows
- acceptable recovery guarantee:
  - no loss of `paper_id`, `notion_page_id`, `upstream_fingerprint`, or `zotero_item_key` for records still reachable in Notion or Zotero

Crash-recovery rule for partial export success:

- export workflow must set Notion `Zotero 状态=导出中` before any Zotero API write
- if Zotero write succeeds but final Notion write-back fails, next export run must query stale `导出中` items and reconcile by matching Zotero machine tags (`pf:id:<paper_id>`)
- if stale `导出中` reconciliation finds no Zotero machine tag and no matching `Zotero Item Key`, exporter must transition the page to `导出失败` with `Export Error=Aborted before Zotero write; retry required`
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
- `raw_abstract_source` if `raw_abstract` is present
- `upstream_fingerprint`
- `ingested_at`

`authors` format:

- ordered list of creator objects
- each creator object contains:
  - `full_name`
  - `given_name` if parseable
  - `family_name` if parseable
- first-author extraction for dedupe uses the first creator object's `family_name`, else `full_name`
- a display-only short authors string may be derived later, but canonical record storage remains structured

`upstream_fingerprint` contract:

- `upstream_fingerprint = sha256_hex(canonical_json_subset)`
- `canonical_json_subset` is the stable-key-order JSON serialization of:
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
  - `raw_abstract`
  - `raw_abstract_source`
- ingest compares the freshly computed fingerprint to the stored fingerprint to decide whether machine-owned paper content changed
- if the fingerprint is unchanged, ingest may skip machine-field rewrites and only refresh `Last Synced At`

### 5.1 Identity Rules

`paper_id` generation priority:

1. DOI
2. normalized canonical URL
3. stable hash of normalized `title + journal + published_date`

Rules:

- `paper_id` is the canonical machine identifier.
- `notion_page_id` is a downstream location identifier, not a business key.
- Current mixed usage of `id`, `link`, and raw title must be retired.
- once assigned, `paper_id` is immutable
- `paper_id` must use only lowercase ASCII characters from `[a-z0-9:._/-]`
- `paper_id` maximum length is 191 characters
- if a DOI-derived identifier would exceed 191 characters, use `doi-hash:<sha256(doi_norm)>` as `paper_id` and persist the full DOI in `identity_aliases`
- if a DOI-derived identifier would contain characters outside `[a-z0-9:._/-]`, use `doi-hash:<sha256(doi_norm)>` as `paper_id` and persist the full DOI in `identity_aliases`

Retroactive DOI discovery rule:

- if a later ingest discovers a DOI for a paper already stored under a non-DOI `paper_id`, the existing `paper_id` is preserved
- normalized DOI is added to `identity_aliases`
- future identity resolution must consult `identity_aliases` before generating a new `paper_id`
- Zotero machine tag `pf:id:<paper_id>` never changes retroactively
- phase 1 resolves `identity_aliases` by building one in-memory alias lookup map from the full registry at workflow start; no separate persisted alias index is required

Compensating uniqueness controls:

- before create, ingest workflow must exact-filter Notion by `paper_id`
- if exact filter returns:
  - `0` pages: create
  - `1` page: update
  - `>1` pages: fail that record and write duplicate audit
- weekly ingest audit must scan for duplicate `paper_id` values after batch completion
- duplicate findings must emit a machine-readable report under `state/duplicate_audit/`
- duplicate audit reports live on the `automation-state` branch, not the default branch
- duplicate findings mark the run degraded and notify the operator, but must not roll back already completed page writes for that run
- subsequent records with duplicate exact-match results continue to fail per-record until operator repair resolves the duplicates

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

- DOI case:
  - `paper_id = "doi:" + doi_norm`
- DOI hash case:
  - `paper_id = "doi-hash:" + sha256_hex(doi_norm)`
- normalized-URL case:
  - `paper_id = "url:" + sha256_hex(url_norm)`
- fallback hash case:
  - `paper_id = "hash:" + sha256_hex(title_norm + "|" + journal_norm + "|" + date_norm)`

Hash encoding rule:

- all `sha256_hex(...)` outputs are lowercase hexadecimal strings

## 6. Notion Database Design

Use one primary data source called `Papers`.

Schema bootstrap requirement:

- database creation and option seeding must be performed by a bootstrap script checked into the repo
- manual one-off creation is allowed only for initial integration sharing, not for schema definition
- bootstrap script must print the resolved Notion database ID as a machine-readable output
- bootstrap script must read the parent page from `NOTION_PARENT_PAGE_ID`
- operator must store that value as GitHub Actions secret `NOTION_PAPERS_DATABASE_ID` in the private automation mirror before any scheduled workflow is enabled
- runtime workflows must never try to discover the production database ID by search
- bootstrap option seeding for `核心话题` must come from `web/categories.json` `topics[].name`
- bootstrap script does not create or configure Notion database buttons; button setup is a manual one-time workspace step when that feature is available

Bootstrap script contract:

- bootstrap script must be idempotent
- if `NOTION_PAPERS_DATABASE_ID` is supplied and resolvable, script validates and repairs that database instead of creating a new one
- if no database ID is supplied, script may create exactly one database under the configured parent page and return its ID
- script must exit non-zero on schema drift it cannot repair automatically
- minimum integration capability requirement is permission to read, update, and insert content under the target parent/page and the resulting `Papers` data source
- bootstrap script must perform a capability probe before mutation and emit a structured error distinguishing authentication failure from missing authorization scope
- capability probe must at minimum:
  - read the configured parent page
  - verify create permission by creating a temporary scratch child page under `NOTION_PARENT_PAGE_ID`, then immediately archiving that scratch page before database creation
- automatic repair may add missing properties and missing select options
- automatic repair must not rename unknown properties, delete existing properties, or coerce mismatched property types in place; those cases fail fast for operator review

### 6.1 Properties

- `标题` (`title`)
- `状态` (`select`): `待看`, `收藏`, `忽略`
- `Zotero 状态` (`select`): `未导出`, `待导出`, `导出中`, `已导出`, `导出失败`
- `标题中文` (`rich_text`)
- `研究方法` (`select`) with pre-seeded options:
  - `Experiment`
  - `Archival`
  - `Theoretical`
  - `Review`
  - `Qualitative`
  - `Unclassified`
- `核心话题` (`multi_select`)
- `Authors JSON` (`rich_text`) storing canonical serialized creator array
- `发布日期` (`date`)
- `期刊` (`rich_text`)
- `来源` (`rich_text`)
- `Canonical URL` (`url`)
- `DOI` (`rich_text`)
- `paper_id` (`rich_text`)
- `Upstream Fingerprint` (`rich_text`)
- `Ingested At` (`date`)
- `Last Synced At` (`date`)
- `Export Started At` (`date`)
- `Zotero Item Key` (`rich_text`)
- `Exported At` (`date`)
- `Export Error` (`rich_text`)
- `人工锁定` (`checkbox`)

Optional fields:

- `作者摘要` (`rich_text`) if a short authors string is useful for views
- `来源类型` (`select`) if later needed for source-specific handling

Author overflow rule:

- `Authors JSON` is stored as a single `rich_text` fragment with an operational cap of 1800 Unicode code points measured on the serialized JSON string
- if serialized `Authors JSON` exceeds 1800 characters, property value becomes `@page-body:SYSTEM_METADATA_JSON`
- full structured creators are then stored under the `SYSTEM_METADATA_JSON` block using the chunking protocol below
- exporter must resolve this sentinel before building Zotero `creators`
- if sentinel resolution fails because the block is missing or malformed, exporter must set `Zotero 状态=导出失败`, write a concise `Export Error`, and append details under `EXPORT_AUDIT`

Export error overflow rule:

- `Export Error` stores only the last concise user-visible summary, capped at 500 characters
- detailed failure payloads and stack traces are written under `EXPORT_AUDIT`

### 6.2 Page Body

Long text should live in the page body, not properties:

- system raw abstract
- system metadata json
- user notes
- audit annotations

Page body block ownership is fixed:

- ingest workflow owns `SYSTEM_RAW_ABSTRACT`
- ingest workflow owns `SYSTEM_METADATA_JSON`
- ingest workflow owns `INGEST_AUDIT`
- export workflow owns `EXPORT_AUDIT`
- user owns `USER_NOTES`

Jobs may only rewrite their own anchored block sections. Jobs must not rewrite or reorder blocks owned by another actor.

Audit retention rule:

- `INGEST_AUDIT` and `EXPORT_AUDIT` retain only the most recent 50 dated entries each
- after each append, the workflow trims older entries from its owned audit block
- detailed stack traces stay in workflow logs, not in Notion page bodies
- workflows must paginate block children to exhaustion when locating anchors or trimming audit entries
- audit trimming must collect the owned child block IDs under each anchor and delete overflow entries by block ID, oldest first

Anchor mechanism:

- page body starts with exact Heading 1 blocks in this order:
  - `SYSTEM_RAW_ABSTRACT`
  - `SYSTEM_METADATA_JSON`
  - `INGEST_AUDIT`
  - `EXPORT_AUDIT`
  - `USER_NOTES`
- workflows locate anchors by exact heading text
- anchor discovery must paginate `retrieve block children` until exhaustion; reading only the first page is invalid
- if a required system anchor is missing, the workflow recreates it at its designated ordered position among system anchors and appends an audit line explaining the repair
- workflows may modify only blocks under their anchor until the next system anchor
- if duplicate system anchors exist, workflow must keep the first valid anchor, append a repair note, and ignore later duplicates for that run

`SYSTEM_RAW_ABSTRACT` contract:

- if `raw_abstract` is present, the first block under `SYSTEM_RAW_ABSTRACT` must be a paragraph with exact prefix `source: `
- the value after `source: ` is the canonical `raw_abstract_source`
- subsequent blocks under `SYSTEM_RAW_ABSTRACT` contain the abstract text content
- if no raw abstract exists, the anchor remains empty
- allowed `raw_abstract_source` values in phase 1 are:
  - `rss`
  - `publisher`
  - `crossref`
  - `semantic_scholar`
  - `user_pasted`
  - `unknown`
- new or unmapped sources normalize to `unknown` until the controlled vocabulary is intentionally extended

`SYSTEM_METADATA_JSON` contract:

- the first block under `SYSTEM_METADATA_JSON` must be a JSON code block manifest
- manifest top-level object must include:
  - `schema_version`
  - `creators_storage`
- if `creators_storage=inline`, manifest must also include `creators`
- if `creators_storage=chunked`, manifest must also include `creators_chunks`
- `creators` stores the canonical ordered author array using the creator-object shape defined in Section 5
- when chunked storage is used, the next `creators_chunks` code blocks must each begin with exact first line `CREATORS_CHUNK i/n`
- exporter reconstructs the creator array by parsing `CREATORS_CHUNK i/n`, sorting by `i`, validating a contiguous `1..n` sequence with no gaps, concatenating the remaining chunk payload text, and JSON-parsing the result
- each chunk payload should stay under 1800 characters
- `creators_chunks` must not exceed 25 in phase 1
- if the creator payload would require more than 25 chunks, ingest fails that record's author write, logs `authors_too_large` under `INGEST_AUDIT`, and leaves export unavailable until manual intervention
- manual intervention for `authors_too_large` is:
  - operator curates a reduced creator list or external note manually under `SYSTEM_METADATA_JSON`
  - operator then reruns `FORCE_METADATA_REPAIR` or re-queues export after confirming the payload fits the phase 1 chunk limits
- other machine-owned keys may be present in the manifest, but exporter depends only on the creator fields above
- if multiple manifest blocks exist under `SYSTEM_METADATA_JSON`, exporter reads only the first valid manifest and logs an audit repair note

### 6.3 Property Ownership Matrix

Ingest workflow owns:

- `标题`
- `标题中文`
- `研究方法`
- `核心话题`
- `Authors JSON`
- `发布日期`
- `期刊`
- `来源`
- `Canonical URL`
- `DOI`
- `paper_id`
- `Upstream Fingerprint`
- `Ingested At`
- `Last Synced At`
- create-time initialization of `状态=待看`
- create-time initialization of `Zotero 状态=未导出`

Export workflow owns:

- `Export Started At`
- `Zotero Item Key`
- `Exported At`
- `Export Error`

User owns:

- `状态`
- `Zotero 状态` request transitions into `待导出`
- `人工锁定`
- page-body `USER_NOTES`

Shared field rule for `Zotero 状态`:

- ingest workflow may set `Zotero 状态=未导出` only on page creation as bootstrap initialization
- user may set `未导出 -> 待导出` or `已导出 -> 待导出`
- export workflow owns all terminal and in-progress transitions:
  - `待导出 -> 导出中`
  - `导出中 -> 已导出`
  - `导出中 -> 导出失败`
  - invalid pending states back to `未导出`
- ingest workflow must never modify `Zotero 状态`

Lock behavior:

- if `人工锁定=true`, ingest workflow must not overwrite descriptive user-visible fields
- in locked state, ingest may only update `Upstream Fingerprint` and `Last Synced At`
- if upstream canonical metadata changed while locked, ingest writes an audit note under `INGEST_AUDIT` instead of overwriting display fields
- locked state does not prevent user-triggered `Zotero 状态` changes
- ingest must not mutate `Authors JSON` or `SYSTEM_METADATA_JSON` for pages currently in `Zotero 状态=导出中`
- if ingest detects upstream author metadata drift for a page in `导出中`, it must update `Upstream Fingerprint`, append an `INGEST_AUDIT` note, and leave author-bearing fields untouched until export reaches a terminal state
- if export reaches `导出失败` because `SYSTEM_METADATA_JSON` or author data is malformed, operator repair path is:
  - if `人工锁定=true`, operator temporarily clears the lock before repair and may restore it after repair completes
  - if `Zotero 状态=待导出`, operator first resets it to `未导出`
  - run manual `ingest-to-notion` dispatch for that `paper_id` with `FORCE_METADATA_REPAIR=true`
  - let ingest rebuild `Authors JSON` and `SYSTEM_METADATA_JSON`
  - then re-queue export by setting `Zotero 状态=待导出`
- `FORCE_METADATA_REPAIR=true` is valid only with an explicit `paper_id_filter`
- `FORCE_METADATA_REPAIR=true` must fail fast if `人工锁定=true`
- `FORCE_METADATA_REPAIR=true` applies only to pages in `Zotero 状态=未导出` or `导出失败`
- `FORCE_METADATA_REPAIR=true` must fail fast on pages with `Zotero Item Key` already present; operator must review and explicitly re-export those after repair

Timestamp semantics:

- `Ingested At` is immutable after first successful create
- `Last Synced At` is refreshed on every successful ingest reconciliation touching that page
- `Export Started At` is set when export workflow transitions `待导出 -> 导出中`
- `Export Started At` is cleared on terminal export transitions to `已导出` or `导出失败`
- all machine-managed timestamp properties use UTC datetime precision in ISO 8601 form with second precision, not date-only values
- attempt-history timing is preserved in `EXPORT_AUDIT`; `Export Started At` is intentionally reserved for in-flight timer logic only
- Notion updates must be patch-style and may include only owned properties; no workflow may send whole-page replacement payloads
- ingest workflow may set `Ingested At` only on create or when the property is empty due to prior data damage
- "touching" means the paper appeared in the current ingest batch and the workflow successfully resolved its Notion mapping, even if no user-visible descriptive fields changed
- even in locked state, ingest may restore `Ingested At` only if it is empty
- if a previously overflowed author list later serializes to 1800 characters or less, ingest restores the inline `Authors JSON` property and also rewrites `SYSTEM_METADATA_JSON.creators` to the same canonical array for consistency

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
- `忽略 -> 收藏`
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
- if user changes `状态` away from `收藏` while a paper is already `导出中`, the in-flight export is allowed to finish; exporter writes an audit note explaining that export completed after state changed

Transition flow:

- new item: `未导出`
- button click: `待导出`
- export job starts: `导出中`
- success: `已导出`
- failure: `导出失败`
- retry button or manual change: `导出失败 -> 待导出`
- force re-export: `已导出 -> 待导出`
- defensive correction: `待导出 -> 未导出` when export request is invalid because `状态!=收藏`
- defensive correction: `导出中 -> 导出失败` when `Export Started At` is missing or invalid

Migration default:

- all migrated papers, including migrated `收藏`, start with `Zotero 状态=未导出`
- phase 1 never auto-queues historical favorites for Zotero export during migration

## 8. Notion Button Behavior

The Notion button is a UX trigger, not an external execution engine.

Buttons:

- `加入 Zotero`: set `Zotero 状态=待导出`
- `重试导出`: set `Zotero 状态=待导出` from failure state

Creation contract:

- if the target Notion workspace supports database buttons or equivalent property-update automation, implementation creates these two buttons in the primary `Papers` view
- if the workspace plan or feature set does not support that button mechanism, implementation omits buttons and uses the documented manual-property fallback without blocking rollout
- button creation is a manual workspace configuration step, not part of the bootstrap script or API automation

Phase 1 deliberately does not rely on native Notion webhook actions as the primary engine. The actual work is executed by scheduled polling jobs.

Fallback trigger:

- if Notion native button actions are unavailable in the target workspace, user manually edits `Zotero 状态` to `待导出`

User-visible invalid-state handling:

- the Notion button is intended to appear in `收藏`-oriented views only
- if a user still produces `状态!=收藏` with `Zotero 状态=待导出`, export workflow corrects it on the next run
- correction is visible via `Export Error`
- this temporary inconsistency window is accepted in phase 1 because enforcement is batch-driven rather than real-time

Invalid transition handling:

- if export workflow sees `Zotero 状态=待导出` but `状态!=收藏`, it must:
  - set `Zotero 状态=未导出`
  - set `Export Error=Rejected: 状态 must be 收藏 before export`
  - skip Zotero API calls for that page
- the only invalid pending states recognized in phase 1 are:
  - `待导出` with `状态!=收藏`
  - `待导出` with missing `paper_id`
  - `导出中` with missing `Export Started At`
  - `导出中` with `Export Started At` more than 5 minutes in the future relative to runner clock
- invalid `导出中` states must transition to `导出失败` with a concise `Export Error` and an `EXPORT_AUDIT` note
- invalid `待导出` pages with missing `paper_id` must transition to `导出失败` with `Export Error=Missing paper_id; export requires canonical record`
- phase 1 accepts a best-effort cancellation race: if a user changes `Zotero 状态` away from `待导出` after the exporter has already selected the page but before it claims `导出中`, that cancellation may lose within the current polling cycle

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

- every 6 hours

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
- if more than 100 exact-property fallback lookups are needed in one ingest run, abort the run after audit logging and require registry repair before retry

Rate-limit and batching requirements:

- target steady-state Notion request rate: at most 1 request per second
- batch page writes in chunks of at most 50 records before a checkpointed pause
- on `429` or transient `5xx`, use exponential backoff starting at 5 seconds and cap at 60 seconds
- on Notion `400` due to invalid property values, fail fast and log under `INGEST_AUDIT`; do not retry
- on Notion `409`, fail fast for that record, log it, and continue with the rest of the batch
- if AI classification fails for an item, ingest must still create or update the paper using fallback values:
  - `标题中文=""`
  - `研究方法="Unclassified"`
  - `核心话题=[]`
- classification failures must be logged under `INGEST_AUDIT` for later repair; they must not block paper ingestion
- a weekly repair pass may retry `Unclassified` items, but this is optional for phase 1
- if OpenAI is broadly unavailable for a run, ingest continues with fallback values but must:
  - mark the run as degraded
  - emit a failure notification
  - stop further classification attempts after 10 consecutive provider failures
- per-run AI budget cap must be configurable; default cap is 500 paper classifications per run

AI classification contract:

- default model: `gpt-4o-mini`
- output format: strict JSON object
- required keys:
  - `title_zh`
  - `method`
  - `topics`
- `method` must map to the pre-seeded `研究方法` option set; unknown values downgrade to `Unclassified`
- implementation must surface the exact configured model ID in run logs
- model pin review cadence must match the quarterly Notion API compatibility check
- if a dated model snapshot is later adopted in configuration, quarterly review must confirm its availability before it becomes the production default

Topic validation contract:

- the approved topic taxonomy source is the permanent shared artifact `web/categories.json` under `topics[].name`
- implementation must vendor that exact topic-name set into a shared constant module used by ingest, migration, and schema validation
- ingest must cap topics at 3 per paper
- unknown or over-limit topic values must be dropped and logged under `INGEST_AUDIT`
- taxonomy additions require rerunning the bootstrap repair script in test, then production, before the classifier emits the new topic
- taxonomy removals are soft-deprecated in phase 1: Notion may retain old options until an explicit cleanup migration removes or remaps them

### 9.2 `export-zotero`

Purpose:

- query Notion for all pages with `Zotero 状态=待导出`
- query Notion for stale pages with `Zotero 状态=导出中`
- export to Zotero
- write back export result

Schedule:

- every 30 minutes

Responsibilities:

- transition `待导出 -> 导出中 -> 已导出 or 导出失败`
- handle retries safely
- write back `Zotero Item Key`
- process export work in deterministic bounded batches

Query completeness protocol:

- exporter queries Notion by property filters, never by search
- exporter must paginate to exhaustion on every run using `start_cursor`
- no time-based watermark is allowed for pending export discovery
- ordering should be deterministic, using `last_edited_time` ascending
- items left in `导出中` with `Export Started At` older than 20 minutes must be re-evaluated on the next run
- exporter must reconcile stale `导出中` items before processing fresh `待导出` items
- exporter processes at most 100 pages per run after deterministic ordering is established
- pages beyond the cap remain `待导出` and are picked up by the next scheduled run
- stale `导出中` reconciliation counts toward the same cap and always takes precedence over fresh `待导出`

Rate-limit and batching requirements:

- target steady-state Notion request rate: at most 1 request per second
- Zotero writes should be chunked in batches of at most 25 items between state sync checkpoints
- on `429`, `412`, or transient `5xx`, exporter retries with exponential backoff starting at 5 seconds and capping at 60 seconds
- exporter must respect Zotero `Backoff` or `Retry-After` headers when present
- on Zotero update, exporter must first fetch the target item JSON, read that item's `version` field, and then send `If-Unmodified-Since-Version: <item.version>` on the write request
- on Zotero `412`, exporter must refetch the item, compare machine tags and mapped fields, and then retry or fail with audit logging
- Zotero batch size of 25 is a conservative operator limit, not a claimed server-side hard limit
- on transition `待导出 -> 导出中`, exporter must write both `Zotero 状态=导出中` and `Export Started At=<now>` before any Zotero API mutation
- stale-item recovery must use `Export Started At` as the authoritative staleness clock, not Notion `last_edited_time`
- exporter must re-read the page body immediately before constructing Zotero `creators` only when `Authors JSON=@page-body:SYSTEM_METADATA_JSON`
- steady-state export throughput assumptions treat this sentinel-driven page-body re-read as exceptional, not mandatory for every item
- terminal export success write-back must be one Notion PATCH that updates `Zotero 状态=已导出`, `Zotero Item Key`, `Exported At`, `Export Started At=null`, and clears `Export Error`
- terminal export failure write-back must be one Notion PATCH that updates `Zotero 状态=导出失败`, `Export Started At=null`, and writes the concise `Export Error`

### 9.3 Workflow Hardening

Both workflows must include:

- non-top-of-hour cron timing
- `concurrency` protection
- retry with backoff for rate limits and transient HTTP failures
- manual dispatch support
- clear logs and failure notifications
- `ingest-to-notion` manual dispatch must support optional `paper_id_filter` and `FORCE_METADATA_REPAIR` operator-only inputs
- `paper_id_filter` format is a newline-delimited list of canonical `paper_id` values
- `paper_id_filter` narrows the target set before normal caps are applied
- `paper_id_filter` alone does not suspend the 100-fallback-query safeguard or the AI classification cap

Schema validation contract:

- before each batch run, workflow validates:
  - target database ID exists and is reachable
  - required properties exist
  - required property types match the spec
  - required select options exist for `状态`, `Zotero 状态`, and `研究方法`
  - required `核心话题` multi_select options contain at least the approved taxonomy from `web/categories.json`
- on schema validation failure, workflow aborts before processing any records and writes a failure notification
- validation requests are part of the same 1 request-per-second Notion budget and must be serialized before batch processing begins

Export dependency validation:

- before each `export-zotero` batch run, workflow validates:
  - Zotero API key is present
  - target library ID and type resolve successfully
  - write permission is available for a no-op read-plus-version check
- on Zotero dependency validation failure, workflow aborts before claiming any page into `导出中`

Concurrency policy:

- `ingest-to-notion` and `export-zotero` use distinct concurrency groups
- simultaneous execution is allowed and expected
- safety relies on namespaced registry merges and patch-only Notion updates
- phase 1 assumes both workflows may share one Notion integration token, so each workflow is capped at 1 request per second steady state; combined batch traffic is therefore 2 request per second, leaving headroom for serialized validation calls
- workflows are triggered only by `schedule` and `workflow_dispatch`, not by `push`, so commits to `automation-state` do not trigger product loops
- `automation-state` branch must be protected so only GitHub Actions bot pushes are allowed
- on non-fast-forward merge, export workflow must preserve the remote `metadata` object unchanged
- on non-fast-forward merge, ingest workflow may update only `metadata.last_compaction_week` and must preserve all other remote metadata keys

Failure notifications:

- at minimum, failed runs must emit GitHub Actions notifications to the automation mirror maintainer
- degraded runs must include a human-readable summary in workflow logs

API version lifecycle:

- all Notion API calls must read the version string from one shared environment variable `NOTION_API_VERSION`
- phase 1 pins `Notion-Version=2022-06-28`
- phase 1 uses `ZOTERO_API_VERSION=3`
- implementation planning must add a quarterly compatibility check against a test workspace
- if Notion responds with version deprecation or unsupported-version errors, workflows must fail fast and require an explicit version-bump change validated in test before production rollout
- quarterly compatibility review is owned by the automation mirror maintainer
- each quarterly review writes a short result artifact under `state/compatibility/<YYYY-Qn>.md` on `automation-state`
- compatibility report format is:
  - first line `Quarter: <YYYY-Qn>`
  - second line `Status: pass|fail`
  - bullet list of tested APIs and remediation notes
- quarterly review scope must also verify that the configured OpenAI model remains available, and if not, document the replacement model activation

Keepalive policy:

- monthly keepalive automation writes only to `automation-state`
- keepalive commit updates `state/heartbeat.json` or an equivalent non-product file on `automation-state`
- keepalive must never write to the default branch
- keepalive workflow exists only to prevent silent schedule disablement on low-activity repos
- `state/heartbeat.json` minimal shape is:
  - `last_keepalive`
  - `workflow`
- `last_keepalive` uses UTC ISO 8601 datetime format

`keepalive-state` workflow:

- schedule: `17 6 3 * *`
- triggers: `schedule` and `workflow_dispatch` only
- concurrency group: `keepalive-state`
- action: update `state/heartbeat.json` on `automation-state`
- no product workflow may depend on keepalive output

## 10. Zotero Integration Design

Primary integration path: Zotero Web API.

Fallback path: manual RIS/BibTeX export only for exceptional recovery scenarios.

### 10.1 Field Mapping

- `itemType -> journalArticle` for all phase 1 exports
- `标题 -> title`
- `Authors JSON -> creators`
- `发布日期 -> date`
- `期刊 -> publicationTitle`
- `DOI -> DOI`
- `Canonical URL -> url`
- `研究方法 / 核心话题 -> tags`
- `Notion metadata -> extra`
- known phase 1 limitation: sources identified as preprints still export as `journalArticle`; a future `来源类型=preprint` override may change this in phase 2

Phase 1 does not write any value to Zotero `abstractNote`.

Rationale:

- `raw_abstract` remains stored in Notion for operator review and future use
- phase 1 avoids exporting abstracts until provenance and truncation behavior are implemented as a separate contract
- machine metadata is written to Zotero `extra`, not `note`
- `extra` uses stable newline-delimited keys:
  - `Paper Feed ID: <paper_id>`
  - `Notion Page ID: <notion_page_id>`
  - `Canonical Source: <source>`
  - `Canonical URL: <canonical_url>`

### 10.2 Dedup and Idempotency

Dedup order:

1. existing `Zotero Item Key` in Notion
2. machine tag like `pf:id:<paper_id>`
3. DOI match
4. normalized title plus first author plus year match
5. otherwise fail safely

Dedup step 4 normalization:

- title normalization for both canonical records and Zotero items reuses the Section 5.1 title normalization rules
- first author on the Zotero side is taken from the first creator with `creatorType=author`; compare `lastName` if present, else full display name
- year on the Zotero side is the 4-digit year parsed from Zotero `date`; if no year is parseable, dedup step 4 must not match
- if step 4 yields more than one plausible Zotero candidate, exporter must fail safely with audit logging rather than choose arbitrarily

Required protocol:

- record returned `itemKey`
- record the fetched per-item `version` before write and the returned library-level `Last-Modified-Version` after write when relevant
- handle stale writes and conflict responses
- never assume create-only semantics
- if `Zotero Item Key` is already present, force re-export must update the existing Zotero item rather than create a second item
- on successful Zotero write plus failed Notion write-back, leave page in `导出中` and rely on the next run's stale-item reconciliation using Zotero machine tags
- every Zotero create, update, and reconciliation path must ensure machine tag `pf:id:<paper_id>` is present
- if a previously exported Zotero item lacks that machine tag, export workflow must repair the tag before considering recovery complete
- phase 1 has no automatic per-paper retry cap; retries are user-driven by explicitly moving `Zotero 状态` back to `待导出`
- on Zotero `412`, retry exactly once only when:
  - the refetched item still contains machine tag `pf:id:<paper_id>`
  - the item key matches the Notion record's `Zotero Item Key` when that key exists
- otherwise exporter must fail the page to `导出失败` with audit logging and require explicit user/operator review

Phase 1 Zotero target:

- personal library only
- config must provide:
  - `ZOTERO_LIBRARY_TYPE=users`
  - `ZOTERO_LIBRARY_ID=<numeric userID>`
- group libraries are out of scope for phase 1

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

1. Create the private automation mirror, configure `automation-state` and `automation-state-dry-run` branch protection, enable keepalive, and confirm secrets can be stored there.
2. Run pre-migration data quality audit on legacy files.
3. Freeze canonical contract.
4. Create or verify the Notion `Papers` data source in a test workspace.
5. Freeze Notion schema.
6. Bootstrap test workspace with script and validate schema.
7. Extract reusable normalization and identity utilities from current monoliths.
8. Add shadow registry and bootstrap an empty registry in `automation-state`.
9. Add Notion upsert path without removing current outputs.
10. Run dry-run backfill on 50 to 100 records in `MIGRATION_MODE=true`.
11. Validate duplicate handling and identity parity.
- Cutover gate: obtain explicit user sign-off on the legacy interaction mapping, especially `archived -> 状态=待看`, before any production cutover.
12. Promote bootstrap script to production workspace, store production `NOTION_PAPERS_DATABASE_ID`, and re-run schema validation.
13. Enable scheduled `RSS -> Notion` ingest.
14. Enable `Notion -> Zotero` export.
15. Downgrade old web app to projection mode.
16. Remove assumptions that `feed.json` or `interactions.json` is canonical.
17. Disable legacy write endpoints.

### 12.1 Historical Data Migration

Special care is required because current state uses mixed identifiers:

- UI interactions mostly use `link`
- abstracts and classification flows use `id`
- some caches use raw title

Migration script must:

- resolve legacy link-based interactions against canonical `paper_id`
- flag collisions and unresolved records
- never silently merge ambiguous items

Migration-mode ingest rule:

- `MIGRATION_MODE=true` is allowed only for explicit backfill runs
- `MIGRATION_MODE` is supplied only as a `workflow_dispatch` boolean input that is passed into the migration script environment
- scheduled workflows must hard-code `MIGRATION_MODE=false`
- in migration mode, the steady-state safeguard `>100 exact-property fallback lookups abort` is suspended because the registry is expected to be cold
- migration mode still requires exact-property Notion queries and duplicate audits
- once the initial backfill completes and registry parity is validated, all scheduled ingests run with `MIGRATION_MODE=false`
- the default AI classification cap of 500 still applies in migration mode unless the operator supplies an explicit higher `workflow_dispatch` override for that backfill run

Dry-run isolation rules:

- dry-run backfill writes only to the test Notion workspace
- dry-run backfill writes registry state only to `automation-state-dry-run` or an equivalent isolated branch, never to production `automation-state`
- dry-run backfill must not write to Zotero
- production secrets must not be available to dry-run workflows

Disposition flow for unresolved records:

- `auto-resolved`
- `manual-review-required`
- `skipped-preserve-legacy`

Operational owner:

- migration owner prepares collision report
- main agent reviews collision report
- user approves final disposition before cutover

Legacy migration input scope:

- `web/interactions.json`
- `web/abstracts.json` if present
- `web/user_corrections.json` if present
- `web/translations.json`
- `web/feed.json` if present
- `filtered_feed.xml`

Pre-migration data quality audit must report:

- record counts by source file
- presence and count of legacy `archived` interaction state, or the equivalent key if named differently
- distinct `id` / `link` mismatch counts
- malformed URLs
- missing titles
- duplicate DOI counts
- unresolved interaction references

Authority order during migration:

- canonical paper identity comes from normalized feed/XML records plus shared identity rules
- user workflow state comes from `web/interactions.json`
- user classification overrides come from `web/user_corrections.json`
- abstract text provenance comes from `web/abstracts.json`
- title translation and lightweight AI labels come from `web/translations.json` only when no stronger override exists

Legacy interaction mapping:

- `favorites` -> `状态=收藏`
- `archived` -> `状态=待看` plus migration audit note `source=archived`
- `hidden` -> `状态=忽略`
- items with no interaction record -> `状态=待看`

Collision threshold:

- halt migration if unresolved collisions exceed `max(2, min(10, floor(0.02 * processed_records)))`
- `processed_records` means the number of canonical records for which migration resolution was attempted in the current run
- rationale: phase 1 halts early on ambiguity because user-approved manual review is cheaper than silently merging identity collisions

Preservation for unresolved legacy records:

- unresolved mappings with disposition `skipped-preserve-legacy` must be exported to:
  - `state/legacy_unresolved.json`
  - `state/legacy_unresolved.csv`
- these unresolved exports live on the `automation-state` branch, not the default branch
- user must review these exports before legacy write endpoints are disabled

### 12.2 Rollback and Reconciliation

Rollback triggers:

- sample migration parity failure
- unresolved collision rate above agreed threshold
- duplicate creation in test Notion workspace
- export reconciliation failure on test batch

Rollback actions:

- stop Notion upsert workflow
- stop Zotero export workflow
- keep legacy UI in current mode
- preserve `automation-state` branch and all migration reports for audit
- record any already-created Zotero item keys in rollback audit notes so later reconciliation can reuse them instead of duplicating export
- preserve already-created Notion pages and treat them as reusable by `paper_id` on the next migration retry; rollback does not require deleting them
- for dry-run test workspaces only, operator may optionally archive migrated pages with an admin cleanup script before rerunning
- do not disable legacy write endpoints until rollback issues are resolved and user re-approves cutover

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
- add a monthly keepalive commit or equivalent repository activity on the private automation repo or mirror so scheduled workflows do not silently age out
- keepalive must be automated, not manual
- keepalive writes only to `automation-state`, never to the default branch

## 15. Security Requirements

Minimum phase 1 requirements:

- workflows containing Notion, Zotero, or OpenAI secrets must run only from a private repo or a private deployment mirror
- Notion integration token must be scoped only to the target workspace/database access required
- Zotero credentials must be limited to the intended personal library context
- OpenAI secret is available only to ingest workflow, not export workflow
- secrets must be separated by workflow instead of one shared global environment
- exception: operator-only `rebuild_registry` runs in a dedicated GitHub Actions environment named `admin` that has temporary access to both Notion and Zotero secrets
- `rebuild_registry` must never run as a scheduled workflow
- workflow logs must never print tokens, raw auth headers, or full sensitive payloads
- rotate Notion, Zotero, and OpenAI secrets before production cutover and at least every 180 days thereafter
- repo visibility or private mirror status must be verified at checkpoint 1 before any secret is configured
- bootstrapped Notion database ID is stored as secret `NOTION_PAPERS_DATABASE_ID` in the private automation mirror
- `automation-state` branch protection must deny direct human pushes and allow GitHub Actions bot only
- `NOTION_API_VERSION` is a non-sensitive environment variable rather than a secret because it is a public protocol constant, not workspace-specific data

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
  - `rebuild_registry`
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

- dry-run migration sample of 50 to 100 records produces 0 silent merges
- dry-run migration sample of 50 to 100 records has 100% parity on required canonical fields
- sample export run of 20 valid `待导出` items completes with 100% deterministic terminal states (`已导出` or `导出失败`)
- replay from `state/registry.json` can rebuild Notion page mapping for the sample without using Notion search
