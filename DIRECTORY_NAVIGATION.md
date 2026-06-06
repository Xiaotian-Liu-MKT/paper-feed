# Paper Feed Directory Navigation

## Workspace Purpose

- Project root: `C:\Users\91784\PycharmProjects\paper-feed`
- Task type: software
- Cleanup scope: current project root
- Current purpose of this folder: local browser-first academic paper RSS screening app. The project fetches RSS feeds, filters papers by keywords, analyzes titles with OpenAI when configured, and serves a local web UI for screening, favorites, summaries, and RIS export.

## Recommended Entry Points

- `README.md` - user-facing setup and workflow.
- `run_web.bat` - Windows launcher for refreshing feeds and starting the local server.
- `server.py` - local HTTP API and static web server.
- `get_RSS.py` - RSS fetch, filtering, feed JSON generation, title translation/classification.
- `web/index.html` and `web/app.js` - main browser UI.

## Directory Structure

```text
paper-feed/
├─ paper_feed/              Python package helpers and domain models
├─ scripts/                 Legacy/support automation scripts
├─ tests/                   Pytest and UI smoke tests
├─ web/                     Browser UI and generated web data
├─ docs/                    Project documentation and design notes
├─ state/                   Ignored local runtime state
├─ archive/                 Ignored local cleanup archive
├─ .github/                 GitHub Actions workflow
├─ get_RSS.py               RSS update entry point
├─ server.py                Local server/API entry point
└─ run_web.bat              Windows local launcher
```

## Folder Meanings

### `paper_feed/`

Reusable Python modules for identity, canonical URLs, category handling, and data models.

### `web/`

Frontend app files plus generated local JSON such as `feed.json`, `translations.json`, `interactions.json`, and reports. Generated JSON files are ignored by Git but are required for local browsing.

### `tests/`

Regression tests for identity, canonicalization, RIS export, legacy XML, and browser-facing behavior.

### `archive/`

Local-only archive for old debug scripts, logs, and cleanup reports. This directory is ignored by Git and should not contain active source code.

### `state/`

Local runtime state. It is ignored by Git and should not be used as a stable source artifact.

## Typical Tasks

### Refresh papers

Run `python get_RSS.py` or use `run_web.bat`.

### Start the app

Run `python server.py`, then open `http://localhost:8000`.

### Run tests

Run `pytest` from the project root.

### Inspect frontend behavior

Start `server.py`, then open `web/index.html` through the local server rather than as a raw file.

## Notes For Future Maintenance

- Keep active source files in `paper_feed/`, `web/`, `tests/`, or top-level entry points.
- Keep generated runtime data ignored unless there is a deliberate reason to version it.
- Put one-off debug scripts and old logs in `archive/cleanup-YYYYMMDD/`.
- After moving files, run at least `node --check web\app.js` and `pytest` when frontend or Python behavior could be affected.
