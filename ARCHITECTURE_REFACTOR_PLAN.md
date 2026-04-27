# ARCHITECTURE_REFACTOR_PLAN.md

## 1. Purpose

This document is the architecture map for safely decomposing `story_generator_ui.py` without changing runtime behavior.

This is not an implementation pass. No code is changed by this plan. The purpose is to prepare safe, one-step extraction passes that keep the existing Long Document LLM Workstation working while reducing the current god-file risk.

The guiding rule is: one operational step per Codex pass. No big-bang refactors.

The immediate goal is to define stable boundaries around:

- UI and Tkinter state
- workflow runners
- provider/API access
- config/state handling
- threading and queue events
- SQLite history wiring
- legacy compatibility

## 2. Current Architecture Map

### UI / Tkinter Layer

Current location: `story_generator_ui.py`, mostly inside `StoryGeneratorApp`.

Responsibilities currently mixed into the UI file:

- root app/window construction
- theme/style setup
- left workflow tabs
- contextual right-side workspace tabs
- footer controls
- provider/model controls
- cloud routing/cost controls
- story generation controls
- rewrite controls
- translation controls
- QA/validation controls
- prompt tools controls and text areas
- logging widgets
- live output widgets
- chunk/segment table widget
- validation report display
- file picker buttons
- open-output-file helpers
- config field bindings through `self.vars`
- UI-to-config collection via `collect_config()`
- config-to-UI population via `populate_from_config()`
- scheduled cost estimate refresh
- button enable/disable state via `set_running()`

The contextual workspace refactor is already done. The UI is still dense, but the right-side workspace now changes based on the selected left workflow.

### Workflow / Runner Layer

Current location: `story_generator_ui.py`.

Runner classes still inside the UI file:

- `LLMRunner`
- `StoryGenerator`
- `ChunkedRewriter`
- `PromptEnhancer`
- `TranslationRunner`

There is no current class named `TranslationEngine`; the active translation workflow class is `TranslationRunner`.

Responsibilities currently handled by these runners:

- building OpenAI-compatible clients through provider helpers
- streaming chat completions
- retrying rate limits / transient failures
- posting queue events
- checking `stop_event`
- writing partial/final output files
- story continuation logic
- rewrite precleaning and chunk rewriting
- rewrite manifest creation and retry safety checks
- prompt enhancement
- translation profile loading
- translation segment parsing
- translation chunking
- translation manifest creation
- translation output rebuild
- validation after translation

The runners do not directly mutate Tk widgets. They communicate through queue events, which is the main reason extraction is feasible.

### Provider / API Layer

Current primary module: `providers.py`.

Responsibilities already separated:

- `ProviderProfile`
- provider presets
- OpenRouter/cloud OpenAI-compatible setup
- LM Studio local setup
- Ollama local setup
- custom OpenAI-compatible setup
- `build_client()`
- `provider_from_config()`
- `chat_completion_kwargs()`
- `openrouter_extra_body()`
- `list_models()`
- `test_connection()`
- non-streaming response adapter

Current provider behavior:

- OpenRouter routing/cost `extra_body` is only produced for `provider_type == "openrouter"`.
- Local providers use dummy/default key behavior when a real key is not required.
- `provider_smoke_tests.py` exists and passes.
- Actual LM Studio/Ollama runtime behavior still needs optional manual testing against real local servers.
- `provider_events` exists in SQLite schema but provider event rows are not currently wired/populated.

Provider controls are still built in `story_generator_ui.py`.

### Config / State Layer

Current location: `story_generator_ui.py`.

Central config object:

- `GeneratorConfig`

Related config/state helpers:

- `TranslationConfig`
- `load_saved_config()`
- `save_settings()`
- `collect_config()`
- `populate_from_config()`
- `resolve_path()`
- `resolve_optional_path()`

Current behavior:

- JSON config remains the active settings mechanism.
- Existing configs are merged with dataclass defaults.
- Unknown keys are ignored.
- Missing new fields get defaults.
- Primary `api_key` is masked in the UI and blanked on JSON save.
- `default_api_key_value` is still saved and needs review/masking/non-secret-only treatment.
- `GeneratorConfig` combines app-level and project-level settings.

Mixed app/project settings currently include:

- provider preset
- provider type/name/model/base URL
- API env/key fields
- cloud routing and price caps
- history settings
- story prompts and output paths
- rewrite paths, ratios, prompts, and chunk settings
- translation paths, languages, profile settings, glossary/DNT files
- validation output/report settings
- prompt enhancer settings

Current known cleanup issue:

- Saved config may still contain stale absolute paths pointing to old `C:\Users\TestUser\AppData\Local\SyllableShift\...` locations after the project move.
- The moved project is missing the `01_test translation` sanitized sample folder. Treat this as config/sample cleanup or migration work, not built-in sample leakage.

### Threading / Queue / Event Layer

Current location: `story_generator_ui.py`.

Main responsibilities:

- `start_worker()` launches one daemon worker thread at a time.
- `stop_event` controls cancellation.
- Workers send `(kind, payload)` tuples to `self.ui_queue`.
- `process_queue()` runs on the Tk thread through `after(100, ...)`.
- `set_running()` controls button state and progress bar.
- `messagebox.showerror()` is triggered for fatal errors on the Tk thread.

Current event kinds:

- `log`
- `preview`
- `translation_preview`
- `translation_source`
- `translation_output`
- `validation_report`
- `models_list`
- `enhancer_append`
- `enhancer_done`
- `status`
- `error`
- `chunk`
- `segment`
- `done`

Important payload assumptions:

- `log`, `preview`, translation text events, `validation_report`, `status`, and `error` use string payloads.
- `models_list` uses `list[str]`.
- `chunk` and `segment` use dictionaries compatible with `update_chunk_row()`.
- `done` has no meaningful payload and triggers `set_running(False)`.

This queue contract is the most important compatibility surface for runner extraction.

### History / SQLite Layer

Current module: `history_db.py`.

SQLite is already implemented at minimal metadata-history level.

Current capabilities:

- schema version 1
- default DB path: `app_data/workstation_history.sqlite3`
- `HistoryDB`
- `resolve_history_db_path()`
- `redact_config()`
- app-data directory creation
- schema initialization
- newer-schema write disable warning
- run start/end recording through `StoryGeneratorApp.start_worker()`
- redacted config snapshots

Current schema tables:

- `schema_meta`
- `runs`
- `run_files`
- `run_items`
- `validation_issues`
- `provider_events`

Current limitations:

- `run_items` is schema-ready but not fully wired.
- `validation_issues` is schema-ready but not fully wired.
- `provider_events` is schema-ready but not currently populated.
- No history dashboard/UI exists yet.
- SQLite remains optional and must never block app startup.

History redaction behavior:

- removes fields containing `key`, `secret`, `token`, or `password`
- omits major prompt fields
- does not store full documents
- does not store full generated stories, rewritten chunks, translation outputs, or reports

### Legacy / Compatibility Layer

Current files:

- `rewrite.py`
- `original story deepseek.py`
- `story_generator_ui_config.json`

Current legacy behavior:

- `story_generator_ui.py` dynamically loads `rewrite.py` through `importlib.util.spec_from_file_location()`.
- If loaded, `rewrite.py` supplies `SYSTEM_PROMPT`, `preclean_text()`, and `split_into_word_chunks()`.
- Local fallback versions of preclean/chunk splitting exist in `story_generator_ui.py`.
- `original story deepseek.py` is used to read the default story prompt constant.

Compatibility constraints:

- Old JSON configs must continue to load.
- Missing fields must use defaults.
- Unknown keys must be ignored.
- Existing output path behavior should not change during runner extraction.
- The legacy rewrite dynamic loading should be wrapped before it is removed.

## 3. Target Architecture

The target architecture should be reached incrementally. This layout is a direction, not a one-pass migration target.

```text
core/
  workflows.py
  workflow_events.py
  providers.py
  config_models.py
  config_io.py
  history_service.py
  path_utils.py

ui/
  app.py
  tabs/
  panels/
  widgets/
  event_bridge.py

legacy/
  rewrite_adapter.py

tests/
  provider_smoke_tests.py
  test_config_redaction.py
  test_config_roundtrip.py
  test_workflow_boundaries.py
```

Recommended practical transition layout for this repository:

```text
story_generator_ui.py          # temporary UI entry point / compatibility shell
providers.py                   # keep as-is for now; later move to core/providers.py
segmentation.py                # keep as-is for now
translation_profiles.py        # keep as-is for now
translation_validator.py       # keep as-is for now
history_db.py                  # keep as-is for now; later wrap via core/history_service.py
workflow_events.py             # first new small boundary module
workflows.py                   # first extraction target for LLMRunner + small runners
legacy_rewrite_adapter.py      # later replacement for dynamic rewrite.py loading
```

Reason for the practical transition layout:

- Moving files into packages too early can create import churn.
- The first useful win is decoupling runner logic from Tkinter, not renaming every module.
- Existing tests and scripts are simple top-level imports.

### Target Responsibility Boundaries

#### UI Modules

Own:

- Tkinter widgets
- layout
- user interaction
- messageboxes
- file/folder pickers
- visual progress
- queue consumption
- button states
- text areas and tables

Must not own long term:

- LLM streaming loops
- retry logic
- rewrite chunk processing
- translation segment processing
- validation engine internals
- provider request body construction
- SQLite schema details

#### Workflow Modules

Own:

- `LLMRunner`
- `StoryGenerator`
- `ChunkedRewriter`
- `PromptEnhancer`
- `TranslationRunner`
- future workflow-specific helper services

Must communicate outward only through:

- queue/event emitter
- return values for simple headless tests
- exceptions for fatal errors
- filesystem outputs

Must not import Tkinter.

#### Event Bridge

Own:

- event names
- payload dataclasses or typed helper constructors
- conversion from queue payloads to UI actions

Short-term: keep tuple events.

Medium-term: define typed events in `workflow_events.py` while preserving tuple compatibility.

#### Config Modules

Eventual split:

- `AppConfig`: provider settings, base URLs, routing, API env/key metadata, history settings, app behavior
- `ProjectConfig`: workflow file paths, story targets, prompts, rewrite chunk settings, translation profile settings, validation output paths

Short-term: keep `GeneratorConfig` stable until runner extraction is proven.

#### History Service

Own:

- initialization wrapper around `HistoryDB`
- warning/log handling
- run boundary API
- future provider-event/item/validation issue recording

Must not store:

- full documents
- full prompts by default
- generated story bodies
- rewritten chunk text
- translation output text
- API keys or secrets

#### Legacy Adapter

Own:

- loading `rewrite.py`
- exposing stable `default_rewrite_prompt`
- exposing stable `preclean_text()`
- exposing stable `split_into_word_chunks()`
- falling back if `rewrite.py` is missing or broken

Goal:

- isolate dynamic import behavior before removing or deprecating `rewrite.py`.

## 4. Stable Contracts To Preserve

### Queue Event Contract

Runner extraction must preserve the existing queue event kinds and payload shapes.

Current canonical events:

```text
log: str
preview: str
translation_preview: str
translation_source: str
translation_output: str
validation_report: str
models_list: list[str]
enhancer_append: str
enhancer_done: str
status: str
error: str
chunk: dict[str, Any]
segment: dict[str, Any]
done: Any
```

Do not rename events during runner extraction.

Do not make workers call Tkinter directly.

### Worker Lifecycle Contract

Preserve:

- one worker at a time
- `stop_event.clear()` before run
- `stop_event` checked during long work
- UI progress starts before worker
- worker catches exceptions and posts `error`
- worker posts `done` in `finally`
- app remains usable if history DB is disabled/unavailable

### Config Contract

Preserve:

- current JSON config filename until a separate config migration pass
- default merge behavior for missing fields
- ignored unknown keys
- primary `api_key` blanking on save
- history redaction behavior
- existing field names until AppConfig/ProjectConfig split is explicitly designed and approved

### Provider Contract

Preserve:

- OpenRouter-only `extra_body`
- local provider dummy key behavior
- no automated cloud calls in tests
- `provider_smoke_tests.py` behavior
- model list and test connection behavior

### Filesystem Contract

Preserve:

- existing output paths
- relative paths resolving under app root
- partial output write behavior
- rewrite chunk directory behavior
- translation segment directory behavior
- manifest behavior

## 5. Migration Strategy

### Pass A - Document Event Contract

Type: planning or tiny code-only boundary pass.

Goal:

- Create or define `workflow_events.py`.
- Capture event names and expected payload types.
- Add no behavior change.

Risk:

- Very low if it only introduces constants/types and existing code continues using strings.

Tests:

- `python -m py_compile` on active Python files.
- No API calls.

### Pass B - Extract `LLMRunner` + `PromptEnhancer`

Goal:

- Move `LLMRunner` and `PromptEnhancer` into `workflows.py`.
- Keep `GeneratorConfig` in `story_generator_ui.py` for this pass if needed.
- Import the classes back into `story_generator_ui.py`.
- Preserve method names and constructor signatures.

Why first:

- `PromptEnhancer` exercises provider/client/streaming behavior.
- It has fewer filesystem and manifest dependencies than rewrite/translation.

Risks:

- Circular imports if `workflows.py` imports `GeneratorConfig` from `story_generator_ui.py`.
- `OpenAI` import location must stay compatible.

Mitigation:

- Prefer structural typing / `Any` for config in first extraction.
- Move only runner classes and helper dependencies needed for them.
- Do not move `GeneratorConfig` in the same pass.

Tests:

- `python -m py_compile ...`
- app startup smoke test
- prompt enhancer no-API import smoke test
- provider smoke tests

### Pass C - Extract `StoryGenerator`

Goal:

- Move `StoryGenerator` into `workflows.py`.
- Preserve story generation behavior exactly.
- Preserve `preview`, `status`, `log`, and error event behavior.

Risks:

- story prompt injection and continuation marker behavior can regress if helper constants move carelessly.

Tests:

- compile
- app startup
- mock/fake client story streaming test if practical
- no cloud calls

### Pass D - Extract Rewrite Legacy Adapter

Goal:

- Create `legacy_rewrite_adapter.py`.
- Move dynamic `rewrite.py` loading out of `story_generator_ui.py`.
- Expose:
  - `DEFAULT_REWRITE_PROMPT`
  - `preclean_text()`
  - `split_into_word_chunks()`

Why before `ChunkedRewriter`:

- `ChunkedRewriter` depends on legacy rewrite helpers.
- Isolating the legacy boundary reduces extraction risk.

Risks:

- fallback behavior differs from current local fallback functions.

Tests:

- compare adapter preclean/chunk outputs against current behavior on small sample text
- compile
- rewrite import smoke test

### Pass E - Extract `ChunkedRewriter`

Goal:

- Move `ChunkedRewriter` into `workflows.py`.
- Preserve manifest generation, retry safety checks, chunk event payloads, and output rebuild behavior.

Risks:

- high risk due to chunk files, manifest paths, retry selected chunk, ratios, and `chunk` table event payloads.

Tests:

- compile
- chunk preparation smoke test on temp input file
- no API calls
- manifest mismatch warning test if practical
- chunk row payload shape test

### Pass F - Extract `TranslationRunner`

Goal:

- Move `TranslationRunner` into `workflows.py`.
- Keep `TranslationConfig`, profiles, parser, validator behavior stable.
- Preserve translation preview/source/output/report/segment events.

Risks:

- highest extraction risk.
- dependencies include profile loading, segment parsing, glossary/DNT files, protected regexes, validation, segment manifests, and output rebuild.
- moved project currently lacks the `01_test translation` sample folder.

Tests:

- compile
- preview segments with synthetic temp file
- validation smoke test with synthetic segments
- no API calls
- event payload shape tests

### Pass G - Config Split Design

Goal:

- Design `AppConfig` and `ProjectConfig` before implementation.
- Keep backward compatibility with `GeneratorConfig` JSON.

Do not implement in the same pass as runner extraction.

Design requirements:

- separate global provider/history settings from project/workflow data
- define migration from current JSON
- decide how to handle stale absolute paths
- decide how to handle `default_api_key_value`
- decide whether prompt text belongs in project files, config files, or both

### Pass H - API Key Safety Tightening

Goal:

- Preserve current primary `api_key` blanking.
- Review and fix `default_api_key_value` persistence risk.

Possible outcomes:

- relabel as "dummy local key"
- mask the field
- avoid saving non-preset values
- treat it as preset-only provider metadata

This pass should happen before any public packaging or GitHub preparation.

### Pass I - UI Module Split

Goal:

- Only after workflows are extracted, split UI sections into modules.

Likely sequence:

1. move small reusable widget helpers
2. move prompt tools panel
3. move provider panel
4. move workflow tab builders
5. keep `StoryGeneratorApp` orchestration last

Do not begin UI module splitting while runner logic still lives in the same file.

### Pass J - History Service Wrapper

Goal:

- Wrap `HistoryDB` use behind a small service.
- Keep existing schema and DB location.
- Optionally wire `provider_events`, `run_items`, and `validation_issues`.

Do not build the history dashboard in this pass.

## 6. Extraction Order Recommendation

Recommended first coding extraction:

1. `workflow_events.py` constants/types, if desired.
2. `LLMRunner` + `PromptEnhancer`.
3. `StoryGenerator`.
4. legacy rewrite adapter.
5. `ChunkedRewriter`.
6. `TranslationRunner`.

Recommended not first:

- `TranslationRunner`
- `ChunkedRewriter`
- AppConfig/ProjectConfig implementation
- UI module split
- SQLite dashboard
- DOCX bridge

Reason:

The safest first win is to prove that one runner can leave `story_generator_ui.py` without breaking queue/threading/config behavior.

## 7. Risk Matrix

| Area | Risk | Reason | Mitigation |
|---|---:|---|---|
| PromptEnhancer extraction | Low/Medium | Uses provider streaming but limited filesystem behavior | Extract with `LLMRunner`, preserve constructor and queue events |
| StoryGenerator extraction | Medium | Continuation and output append behavior | Add mock stream smoke test |
| ChunkedRewriter extraction | High | Manifest, retry, chunk ratios, legacy helpers | Extract legacy adapter first |
| TranslationRunner extraction | High | Profiles, parser, validator, segment files, many events | Delay until smaller runners prove boundary |
| Config split | High | Current config is monolithic and user-facing | Design separately before implementation |
| UI module split | Medium/High | Tkinter attributes are shared across methods | Do after runners are out |
| SQLite history | Medium | Optional DB should never block app | Preserve current best-effort behavior |
| API key safety | Medium | `default_api_key_value` persists | Dedicated safety pass |

## 8. Test Strategy For Each Refactor Pass

Minimum every pass:

```powershell
python -m py_compile story_generator_ui.py providers.py provider_smoke_tests.py history_db.py history_db_smoke_tests.py segmentation.py translation_profiles.py translation_validator.py rewrite.py "original story deepseek.py"
```

Recommended smoke tests:

- app startup/destroy smoke test
- provider smoke tests
- history DB smoke tests
- validator smoke test with synthetic segments
- no cloud API calls
- no required local LM Studio/Ollama server

Manual UI smoke tests after UI-facing changes:

- switch every left workflow tab
- confirm right workspace tabs still map correctly
- confirm footer remains global
- confirm Start/Stop buttons enable/disable correctly
- confirm model/provider tabs still load
- confirm config save does not store primary `api_key`
- confirm history disabled/invalid DB path does not crash startup

## 9. Non-Goals For Architecture Refactor

Do not include in runner extraction passes:

- DOCX bridge implementation
- SQLite full history dashboard
- vector/document library
- Electron/Qt rewrite
- new workflow features
- project/workspace implementation before config design
- changing prompts/profiles/validators
- changing provider request behavior
- changing output file formats
- changing config file format unless in a dedicated config migration pass

## 10. Open Questions

1. Should `providers.py`, `segmentation.py`, `translation_profiles.py`, `translation_validator.py`, and `history_db.py` remain top-level for now, or move into `core/` only after runner extraction?

Recommendation: keep them top-level for the first extraction to avoid import churn.

2. Should `GeneratorConfig` move before or after workflow runners?

Recommendation: after at least one runner extraction. Moving config first increases blast radius.

3. Should queue events become dataclasses immediately?

Recommendation: no. First define constants/types while preserving tuple events. Convert later only if tests cover the bridge.

4. How should stale absolute config paths be handled?

Recommendation: design as part of Config Split Design. Do not silently rewrite user config during runner extraction.

5. How should missing sanitized sample files be handled?

Recommendation: treat samples as optional. Built-in profile should remain synthetic and functional with generic fallback. Any sample restoration should be a separate sample/config cleanup pass.

6. Should `rewrite.py` be deleted?

Recommendation: no immediate deletion. First wrap it in a legacy adapter, then deprecate after behavior is covered by tests.

## 11. Recommended Next Single Operational Step

Proceed with a small boundary pass, not a broad refactor:

Create a minimal workflow boundary plan or implementation pass for `workflow_events.py` plus extracting `LLMRunner` and `PromptEnhancer` only.

If the next pass must remain planning-only, create a short implementation checklist for:

- files to edit
- exact imports to add/remove
- queue events to preserve
- smoke tests to run
- rollback criteria

Do not extract `ChunkedRewriter` or `TranslationRunner` first.

