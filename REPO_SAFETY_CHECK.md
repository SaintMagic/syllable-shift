# Repository Safety Check

Date: 2026-04-27

This file records the repository hygiene pass before GitHub initialization/upload.

## Added Ignore Rules

The `.gitignore` excludes:

- Python caches: `__pycache__/`, `*.pyc`
- virtual environments: `.venv/`, `venv/`
- secret/config files: `.env`, `story_generator_ui_config.json`, `*api_key*`
- SQLite/runtime data: `*.sqlite3`, `*.db`, `app_data/`
- generated outputs: `output/`, `outputs/`, `rewrite_chunks/`, `translation_segments/`
- logs: `*.log`
- deprecated private/obsolete area: `obsolete-DNU/`

## Safety Scan Summary

Scanned source/docs for obvious:

- API key/token patterns
- bearer tokens
- real-looking client/company names from the known risk list
- real-looking study/protocol IDs from the known risk list
- real-looking WU/work-order IDs from the known risk list
- personal local paths
- generated output folders
- SQLite DB files
- logs

No obvious API key pattern was found in source/docs.

Known local/private runtime artifacts present and intentionally ignored:

- `story_generator_ui_config.json`
- `app_data/workstation_history.sqlite3`
- `__pycache__/`

Known path caveat:

- Planning docs mention stale old local paths under `C:\Users\TestUser\AppData\Local\SyllableShift\...` as part of previously documented migration/safety notes.
- The live config file may contain stale absolute paths and must not be committed.

## Do Not Commit

- `story_generator_ui_config.json`
- `.env`
- anything matching `*api_key*`
- `app_data/`
- `*.sqlite3`
- `*.db`
- `__pycache__/`
- generated story/rewrite/translation outputs
- private client/source documents
- logs
- `obsolete-DNU/`

## Before Upload

Run:

```powershell
git status --ignored
```

Confirm ignored runtime artifacts are not staged. If uncertain about a file, leave it unstaged and review manually.

