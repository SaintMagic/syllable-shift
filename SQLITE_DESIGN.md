# SQLite Design: Metadata and Run History Only

## Goals

- Track run history for story generation, rewrite, translation, validation, prompt tools, and provider tests.
- Store metadata that helps resume, audit, compare, and debug long-document workflows.
- Keep large source files, chunk files, translated outputs, story outputs, validation reports, and logs on disk.
- Preserve the existing JSON config file as the active settings mechanism.
- Make the database optional and safe to add later without changing current workflow behavior.

## Non-Goals

- Do not store full documents, prompts, generated stories, rewritten chunks, translated outputs, or validation reports in SQLite.
- Do not replace `story_generator_ui_config.json`.
- Do not store API keys or secrets.
- Do not implement a document library, vector store, cache, or search index.
- Do not require SQLite for the app to start.
- Do not add SQLite runtime code until UI/provider behavior is stable.

## Proposed DB File Location

Default file for the current portable app:

```text
<app_root>/app_data/workstation_history.sqlite3
```

`<app_root>` means:

- the directory containing the packaged executable when the app is frozen
- otherwise, the directory containing the main script

The app should create `<app_root>/app_data` if it is missing.

Rationale:

- It keeps the portable build self-contained.
- It is easy to back up with the app folder.
- It avoids scattering metadata while the app is still a portable workstation tool.

A future installed-app build may optionally use the OS user-data directory instead,
but that is not the default for the current portable version.

Future config field, if implemented later:

```json
{
  "history_db_file": "app_data/workstation_history.sqlite3"
}
```

This field should be optional. Missing field means use the default path.

## Schema Versioning

Use a tiny schema metadata table:

```sql
CREATE TABLE schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
```

Required keys:

- `schema_version`: integer stored as text, starting at `1`
- `created_at`: UTC ISO timestamp
- `updated_at`: UTC ISO timestamp

Migration policy:

- Open DB.
- Read `schema_meta.schema_version`.
- Apply ordered migrations one version at a time.
- Each migration must be idempotent where practical.
- Never delete user history in automatic migrations.
- If DB is newer than app supports, disable DB writes and show a friendly warning.

## Tables

### `runs`

One row per user-triggered workflow run.

```sql
CREATE TABLE runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_uuid TEXT NOT NULL UNIQUE,
    workflow_type TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    elapsed_seconds REAL,
    title TEXT,
    model TEXT,
    provider_name TEXT,
    provider_type TEXT,
    base_url TEXT,
    config_snapshot_json TEXT,
    config_snapshot_sha256 TEXT,
    input_file TEXT,
    output_file TEXT,
    report_file TEXT,
    working_dir TEXT,
    segments_dir TEXT,
    manifest_file TEXT,
    prompt_tokens_est INTEGER,
    completion_tokens_est INTEGER,
    total_tokens_est INTEGER,
    cost_base_est REAL,
    cost_recharge_est REAL,
    error_summary TEXT,
    notes TEXT
);
```

Allowed `workflow_type` values:

- `story`
- `rewrite`
- `translation`
- `validation`
- `prompt_enhancer`
- `provider_test`
- `model_list`

Allowed `status` values:

- `running`
- `completed`
- `failed`
- `stopped`
- `partial`

Notes:

- `config_snapshot_json` stores selected non-secret settings only.
- `api_key` must never be included.
- Paths are stored as strings and may be absolute or app-relative.
- Full document text is never stored here.

### `run_files`

Files associated with a run.

```sql
CREATE TABLE run_files (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    file_role TEXT NOT NULL,
    path TEXT NOT NULL,
    sha256 TEXT,
    size_bytes INTEGER,
    created_at TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
```

Suggested `file_role` values:

- `input`
- `output`
- `cleaned_input`
- `chunk_source`
- `chunk_output`
- `translation_segment_source`
- `translation_segment_output`
- `manifest`
- `validation_report`
- `log_export`

Notes:

- This table stores file metadata only.
- Hashes are optional because large files can make hashing slow.

### `run_items`

Chunk/segment-level metadata for rewrite and translation.

```sql
CREATE TABLE run_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    item_type TEXT NOT NULL,
    item_id TEXT NOT NULL,
    ordinal INTEGER,
    input_words INTEGER,
    output_words INTEGER,
    ratio REAL,
    status TEXT,
    finish_reason TEXT,
    validation_status TEXT,
    issue_count_error INTEGER DEFAULT 0,
    issue_count_warning INTEGER DEFAULT 0,
    source_file TEXT,
    output_file TEXT,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
```

Allowed `item_type` values:

- `rewrite_chunk`
- `translation_segment`
- `translation_chunk`
- `validation_segment`

Notes:

- `item_id` can be `1`, `0001`, or another profile-specific segment ID.
- `source_file` and `output_file` point to disk files.
- The table does not store source/output body text.

### `validation_issues`

Structured validation issue summary.

```sql
CREATE TABLE validation_issues (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    item_id TEXT,
    severity TEXT NOT NULL,
    category TEXT NOT NULL,
    message TEXT NOT NULL,
    token TEXT,
    source_count INTEGER,
    output_count INTEGER,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
```

Allowed `severity` values:

- `critical`
- `error`
- `warning`
- `info`

Notes:

- This mirrors `ValidationIssue` metadata.
- It may duplicate the saved Markdown/text report in structured form.
- It should not store surrounding segment text.

### `provider_events`

Optional metadata for provider tests and model listing.

```sql
CREATE TABLE provider_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id INTEGER NOT NULL,
    event_type TEXT NOT NULL,
    provider_name TEXT,
    provider_type TEXT,
    base_url TEXT,
    model TEXT,
    success INTEGER NOT NULL,
    message TEXT,
    returned_model_count INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (run_id) REFERENCES runs(id) ON DELETE CASCADE
);
```

Allowed `event_type` values:

- `test_connection`
- `list_models`

## Relationships

- `runs` is the parent table.
- `run_files.run_id` references `runs.id`.
- `run_items.run_id` references `runs.id`.
- `validation_issues.run_id` references `runs.id`.
- `provider_events.run_id` references `runs.id`.
- Deleting a run deletes only its metadata rows, not files on disk.

## Example Records

### Story Run

`runs`:

```text
workflow_type: story
status: completed
title: Original novella draft
provider_name: OpenRouter
provider_type: openrouter
model: deepseek/deepseek-v4-flash
input_file: null
output_file: deepseek_original_novella.md
prompt_tokens_est: 12000
completion_tokens_est: 54000
cost_base_est: 0.0168
cost_recharge_est: 0.0215
```

`run_files`:

```text
file_role: output
path: deepseek_original_novella.md
```

### Rewrite Run

`runs`:

```text
workflow_type: rewrite
status: completed
provider_name: LM Studio Local
provider_type: lm_studio
model: local-model
input_file: novel.md
output_file: novel_rewritten.md
working_dir: rewrite_chunks
manifest_file: rewrite_chunks/manifest.json
total_tokens_est: 82000
cost_base_est: null
cost_recharge_est: null
```

`run_items`:

```text
item_type: rewrite_chunk
item_id: 1
ordinal: 1
input_words: 1200
output_words: 1184
ratio: 0.9867
status: OK
finish_reason: stop
source_file: rewrite_chunks/source_001.md
output_file: rewrite_chunks/rewritten_001.md
```

### Translation Run

`runs`:

```text
workflow_type: translation
status: completed
provider_name: Ollama Local
provider_type: ollama
model: llama3.1
input_file: 01_test translation/translation_stress_test_v9_sanitized_bundle/translation_test_source_segments_v9_sanitized.txt
output_file: translation_output.md
segments_dir: translation_segments
manifest_file: translation_segments/manifest.json
total_tokens_est: 24000
```

`run_items`:

```text
item_type: translation_segment
item_id: 0001
ordinal: 1
input_words: 38
output_words: 41
status: Done
finish_reason: stop
validation_status: PASS
source_file: translation_segments/source_segment_0001.txt
```

### Validation Run

`runs`:

```text
workflow_type: validation
status: completed
input_file: 01_test translation/translation_stress_test_v9_sanitized_bundle/translation_test_source_segments_v9_sanitized.txt
output_file: translation_output.md
report_file: translation_validation_report.md
error_summary: 3 errors, 1 warning
```

`validation_issues`:

```text
item_id: 0007
severity: error
category: placeholder
message: Expected 1 occurrence(s), found 0.
token: {patient_name}
source_count: 1
output_count: 0
```

## Migration Plan From Current JSON Config

Current state:

- `story_generator_ui_config.json` remains the source of active settings.
- The SQLite database, when implemented, stores run history and selected snapshots only.

Implementation migration plan:

1. Add optional `history_db_file` config key with default `app_data/workstation_history.sqlite3`.
2. On app startup, load JSON config exactly as today.
3. If history is enabled, initialize SQLite separately after config load.
4. Do not import data from JSON into SQLite as active configuration.
5. For each new run, write a redacted config snapshot into `runs.config_snapshot_json`.
6. Redaction rules:
   - remove `api_key`
   - remove any future secret/token fields
   - keep provider type/name/model/base URL
   - keep workflow settings needed for audit/debug
7. Existing JSON configs must continue to load even if SQLite is missing, corrupt, disabled, or unsupported.

## Risks and Open Questions

- History can become misleading if files are moved or deleted after a run.
- Hashing large files can slow down workflows; hashing should be optional or delayed.
- `config_snapshot_json` could grow large if prompts are included. Default should store settings metadata, not full prompt text, unless explicitly approved later.
- Run title generation needs a simple rule, such as workflow type plus timestamp, unless the UI later adds editable run names.
- Schema should avoid over-modeling workflow details until run history UI requirements are clearer.
- SQLite writes should never block streaming output; if implemented later, writes should happen at run boundaries or through lightweight queued events.
