# Pass 2 Reality Check Against Current Working Files

Date: 2026-04-27

Scope: Compare `MD plans/PLAN_PASS2_COMPREHENSIVE.md` against the current project at `C:\CODING\Novel generator`.

No implementation changes were made during this check.

## 1. story_generator_ui.py Reality

### Actual Size

`story_generator_ui.py` currently has 2,650 lines.

### Major Top-Level Elements

Top-level constants and helpers:

- `APP_DIR`, `APP_NAME`, `APP_VERSION`, `CONFIG_FILE`
- model/config field sets: `FLOAT_FIELDS`, `INT_FIELDS`, `BOOL_FIELDS`
- `MODEL_PRESETS`
- `read_python_constant()`
- `load_rewrite_backend()`
- `resolve_path()`
- `resolve_optional_path()`
- `load_saved_config()`
- `estimate_tokens()`
- `estimate_tokens_from_words()`
- `money()`
- `preclean_text()`
- `split_into_word_chunks()`

Dataclasses:

- `GeneratorConfig`
- `TranslationConfig`

Runner/backend classes still inside this file:

- `LLMRunner`
- `StoryGenerator`
- `ChunkedRewriter`
- `PromptEnhancer`
- `TranslationRunner`

UI shell:

- `StoryGeneratorApp(tk.Tk)`

There is no class literally named `TranslationEngine` in the current file. The equivalent translation workflow implementation is `TranslationRunner`, and it is still inside `story_generator_ui.py`.

### Backend Logic Still Mixed With Tkinter UI

`story_generator_ui.py` still mixes these responsibilities:

- Tkinter window construction, styling, notebooks, widgets, and layout.
- Config loading/saving and validation via `GeneratorConfig`.
- Provider selection and provider UI wiring.
- LLM request execution through `LLMRunner` and subclasses.
- Story generation streaming and continuation handling.
- Rewrite precleaning, chunking, manifest validation, per-chunk rewriting, and output rebuild.
- Prompt enhancer workflow.
- Translation segment preparation, prompt construction, streaming, output rebuild, and validation.
- Cost/token estimates.
- SQLite history initialization and run-boundary recording.
- Worker thread management.
- Queue event production and queue event consumption.
- File picker/open-file operations.

The queue/event contract is currently simple and important:

- Producers put tuples into `self.ui_queue`.
- Known event kinds are `log`, `preview`, `translation_preview`, `translation_source`, `translation_output`, `validation_report`, `models_list`, `enhancer_append`, `enhancer_done`, `status`, `error`, `chunk`, `segment`, and `done`.
- `StoryGeneratorApp.process_queue()` owns all widget updates for those events.

### What Can Be Safely Extracted First

Safest first extraction target after an architecture plan:

- Extract runner/backend classes behind the existing queue contract, without changing event names or payload shapes.
- Start with `LLMRunner` plus the smallest workflow runner, likely `PromptEnhancer` or `StoryGenerator`.
- Keep `GeneratorConfig` importable/stable while extracting.
- Keep `self.ui_queue` and `stop_event` usage exactly as-is during the first physical extraction.

Extraction targets that need more care:

- `ChunkedRewriter`, because it depends on rewrite manifest behavior, legacy `rewrite.py` helper loading, chunk table events, ratio logic, and output rebuild.
- `TranslationRunner`, because it depends on `TranslationConfig`, `TranslationProfile`, `SegmentParser`, glossary/DNT loading, validation, segment table events, output rebuild, and several text-preview queue events.

Extraction should not begin by moving Tkinter widgets or changing config behavior.

## 2. Existing Modules

### providers.py

Purpose: Provider abstraction for OpenAI-compatible endpoints.

Confirmed contents:

- `ProviderProfile`
- provider presets for OpenRouter, LM Studio Local, Ollama Local, and Custom OpenAI-Compatible
- `provider_from_config()`
- `apply_provider_preset_values()`
- `build_client()`
- `openrouter_extra_body()`
- `chat_completion_kwargs()`
- `response_to_stream_chunks()`
- `list_models()`
- `test_connection()`

Provider behavior appears aligned with the local/cloud provider pass:

- OpenRouter-specific `extra_body` is only emitted when `profile.is_openrouter`.
- Local providers use dummy/default API key values when no real key is required.
- `chat_completion_kwargs()` respects provider streaming support and max output token caps.

Open point:

- `LOCAL_PROVIDER_TYPES` is `{"lm_studio", "ollama"}`. Custom endpoints are treated as local only when the base URL starts with localhost/127.0.0.1/[::1].

### segmentation.py

Purpose: Generic segment parsing.

Confirmed contents:

- `Segment`
- `SegmentParser`

This is already separated from the UI. It is a good stable dependency for translation and validation extraction.

### translation_profiles.py

Purpose: Translation profile models and loaders.

Confirmed contents:

- `GlossaryTerm`
- `TranslationProfile`
- `builtin_translation_profiles()`
- `load_translation_profile()`
- `profile_from_dict()`
- `load_glossary()`
- `load_line_list()`
- `compile_regexes()`

The built-in sample profile uses synthetic placeholders such as `FICTIVE_CLIENT_ALPHA`, `FICTIVE_LSP`, `SAMPLE_APP`, `STUDY-000-0001`, `WORKUNIT-ALPHA-001`, and `WORKORDER-ALPHA-001`.

Important current-file mismatch:

- `translation_profiles.py` points to `APP_DIR / "01_test translation" / "translation_stress_test_v9_sanitized_bundle"`.
- In the current moved project, no `01_test translation` folder is present at the project root.
- Because the instruction file is missing, `builtin_translation_profiles()` falls back to the generic task instruction text for the clinical/localization sample profile.

### translation_validator.py

Purpose: Generic translation QA validator.

Confirmed contents:

- `ValidationIssue`
- `ValidationReport`
- `TranslationValidator`

Current behavior is mostly generic/profile-driven:

- segment count/order/missing/extra checks
- delimiter preservation checks
- DNT term preservation
- placeholder preservation
- URL/path/ID/code preservation
- protected regex preservation
- leaked placeholder checks
- translator-note/comment warning
- grouped report and raw report formatting

The validator is not hardcoded to one exact segment count or one exact language. Some default regexes are opinionated but generally reusable.

### history_db.py

Purpose: SQLite metadata/run-history layer.

Confirmed contents:

- `SCHEMA_VERSION = 1`
- `DEFAULT_HISTORY_DB_FILE = "app_data/workstation_history.sqlite3"`
- `resolve_history_db_path()`
- `redact_config()`
- `HistoryDB`

Implemented tables:

- `schema_meta`
- `runs`
- `run_files`
- `run_items`
- `validation_issues`
- `provider_events`

This means the plan claim that SQLite implementation is "not started / deferred" is outdated.

Current limitations:

- Run-boundary recording is wired through `StoryGeneratorApp.start_worker()`.
- `run_items`, `validation_issues`, and `provider_events` are schema-ready but not fully wired from workflow-specific events.
- The active settings source remains JSON config.

### provider_smoke_tests.py

Purpose: Mock-only provider request construction tests.

Confirmed contents:

- fake OpenAI-compatible client classes
- request body assertions
- local/default key assertions
- model-list/connection fallback assertions
- non-streaming response adapter assertion

No real cloud or local server calls are required.

### rewrite.py

Purpose: Legacy standalone rewrite script.

Confirmed contents:

- standalone constants for input/output/model/API key/system prompt
- `preclean_text()`
- `split_into_word_chunks()`
- `create_stream_with_retries()`
- `main()`

`story_generator_ui.py` still dynamically loads `rewrite.py` using `importlib.util.spec_from_file_location()` and uses it as `REWRITE_BACKEND` for `preclean_text()` and `split_into_word_chunks()` when available.

Conclusion: the plan's "legacy rewrite.py fragments development paths" claim is confirmed.

### Obsolete-DNU Files/Folders

No `obsolete-DNU` folder or obsolete-named file/folder is present in the current moved project root.

## 3. PLAN_PASS2_COMPREHENSIVE.md Accuracy

### Executive Summary

Status: confirmed, with updated line count.

The plan says `story_generator_ui.py` is nearly 2,700 lines and acts as a god file. Current reality: 2,650 lines, still combining UI, threading, runners, config, history, cost estimates, and file operations.

### PLAN.md Completion Review

Step 1 - UI Refactor: confirmed.

- Workflow-specific buttons are in workflow tabs.
- Footer contains global controls: status, Save Settings, Stop, progress.
- Right-side workspace is mapped by selected left workflow.

Step 2 - Provider Polish: mostly confirmed.

- Provider abstraction exists.
- OpenRouter `extra_body` is isolated to OpenRouter.
- Local providers have mock-tested key behavior.
- Test/list-model methods exist.
- Remaining caveat: provider test/model list runs are recorded as generic `runs`, but `provider_events` rows are not currently wired.

Step 3 - Translation/Profile Hardening: partially true.

- Built-in profile identifiers in source are synthetic.
- Validator remains generic enough for current use.
- The original sanitized sample directory is missing from the moved project.
- `story_generator_ui_config.json` still contains stale absolute paths pointing to the old `C:\Users\TestUser\AppData\Local\SyllableShift\01_test translation\...` location. This is saved user config, not built-in code, but it is real local-path residue.

Step 4 - SQLite Design: confirmed.

- `SQLITE_DESIGN.md` exists and scopes storage to metadata/history.

Step 5 - SQLite Implementation: false / outdated.

- SQLite implementation is already present in `history_db.py`.
- `GeneratorConfig` includes `history_enabled` and `history_db_file`.
- UI controls exist for history settings.
- Run-boundary metadata recording is wired in `start_worker()`.
- Default DB exists at `app_data/workstation_history.sqlite3`.

### Gemini Review Incorporation

God-file risk: confirmed.

Workflow extraction need: confirmed.

AppConfig vs ProjectConfig split: confirmed as still not implemented.

API key handling claim: partially true / needs precision.

- Primary `api_key` field is masked in UI with `show="*"`.
- `save_settings()` explicitly writes `data["api_key"] = ""`.
- Current `story_generator_ui_config.json` has `"api_key": ""`.
- However, `default_api_key_value` is saved and is displayed as a normal entry. It currently stores dummy local values such as `lm-studio`, but if a user typed a real secret there, it would be persisted.

Legacy `rewrite.py` issue: confirmed.

UI clutter: confirmed, though Step 1 already reduced the worst "everything at once" workspace issue.

### Current Risk Register

Architecture/god-file: confirmed.

Config/project state mixing: confirmed.

- `GeneratorConfig` still combines provider/global settings, workflow settings, prompt text, translation paths, history settings, and output paths.

API key persistence: partially true.

- Primary `api_key` is stripped on save.
- `default_api_key_value` remains a possible secret-storage footgun.

Hard-to-test workflow classes: confirmed.

- Runners are importable with the UI module, but they depend on `GeneratorConfig`, queue messages, stop events, and filesystem side effects.

Runner logic tied to UI/threading: partially true.

- Runners do not directly mutate widgets.
- They are tied to UI through a queue contract and are launched by `StoryGeneratorApp.start_worker()`.
- This is extractable if the queue event names and payload shapes remain stable.

No project/workspace model: confirmed.

Advanced UI clutter: confirmed.

Validation reports too technical: confirmed / subjective.

Legacy files: confirmed for `rewrite.py`.

### Roadmap Accuracy

Pass 2 Step 1 - Architecture Refactor Plan Only: still appropriate, but should be based on this reality check.

Pass 2 Step 2 - Extract Core Runners: appropriate but should explicitly account for:

- `history_db` now existing
- `GeneratorConfig` still living in `story_generator_ui.py`
- `TranslationRunner` name instead of `TranslationEngine`
- queue event names and payloads as public compatibility surface
- legacy `rewrite.py` dynamic helper dependency

Pass 2 Step 3 - Config Split Design: appropriate.

Pass 2 Step 4 - API Key Safety: appropriate, but update scope:

- primary `api_key` is already stripped and masked
- `default_api_key_value` needs review/masking/redaction behavior
- history snapshots already redact fields containing `key`, `secret`, `token`, or `password`

Pass 2 Step 7 - SQLite History UI: needs correction.

- The SQLite layer is no longer future-only.
- A future UI could still be deferred, but the plan should say "SQLite layer exists; history dashboard is future work."

Step 11/12 docs/GitHub: still future-only.

## 4. Focus Checks

### SQLite Implementation Status

Implemented.

Current DB path default:

`app_data/workstation_history.sqlite3`

Current behavior:

- Initializes DB if enabled.
- Creates `app_data` if missing.
- Continues without DB if initialization fails.
- Disables writes if DB schema is newer than supported.
- Records run start/end through `start_worker()`.

### Provider Polish Status

Mostly implemented and mock-tested.

Confirmed:

- `openrouter_extra_body()` returns `None` for non-OpenRouter providers.
- Local provider presets use dummy/default API keys and do not require real keys.
- `test_connection()` tries model listing first and falls back to tiny chat completion.
- `list_models()` uses `/v1/models` through the OpenAI-compatible client.

Needs more inspection later:

- Manual behavior against actual LM Studio/Ollama servers.
- UI display details for local cost/token planning.
- Whether `provider_events` should be wired or left as schema-only.

### Translation/Profile Hardening Status

Partially complete.

Confirmed:

- Source built-in identifiers are synthetic.
- Validator is generic enough for current profile-driven validation.

Current issues:

- The actual sanitized sample folder is absent in the moved project.
- The saved JSON config has stale old absolute paths to `C:\Users\TestUser\AppData\Local\SyllableShift\01_test translation\...`.
- Saved config contains large prompt/instruction text. This is current config behavior and not a code safety issue by itself, but it matters for future AppConfig/ProjectConfig split.

### API Key Saving Behavior

Primary API key:

- UI field exists and is masked.
- `save_settings()` blanks `api_key` before writing JSON.
- Current config stores `"api_key": ""`.

Potential issue:

- `default_api_key_value` is saved. This is safe for built-in local dummy values but unsafe if a user enters a real secret there.

History redaction:

- `history_db.redact_config()` removes fields whose names contain `key`, `secret`, `token`, or `password`.
- It also omits major prompt fields.

### rewrite.py Legacy Status

Confirmed legacy dependency.

- `story_generator_ui.py` dynamically loads `rewrite.py`.
- If available, its `preclean_text()` and `split_into_word_chunks()` override local fallback helpers.
- This makes runner extraction more delicate because rewrite helper behavior is partly external and legacy.

### Real-Looking Sample Identifiers

Current source scan:

- No obvious prohibited real-looking client/study/WU/internal identifiers were found in active `.py`, `.txt`, `.md`, or config files, apart from plan documents intentionally listing scan patterns.
- Approved synthetic identifiers are present.

Caveat:

- `story_generator_ui_config.json` contains real local absolute paths under `C:\Users\TestUser\AppData\Local\SyllableShift\...`. These are stale moved-project paths and should be treated as config cleanup/migration work later, not as built-in sample data.

### Local Provider Wording Safety

Mostly safe.

Provider preset notes say local endpoints are OpenAI-compatible and depend on the user's local server/model. `UI_README.txt` includes the safer caveat that local endpoints depend on the user's local server/model.

Future wording should avoid implying a privacy guarantee beyond "requests are sent to the configured endpoint."

### Does Config Actually Store api_key?

Current saved config stores:

`"api_key": ""`

So the primary API key is not currently stored.

However, config does store:

- `default_api_key_value`
- full prompt fields
- translation instruction text
- stale absolute local paths

## 5. Architecture Refactor Readiness

### Safest First Extraction Target

Recommended first extraction after a written architecture refactor plan:

1. Define the queue event contract explicitly.
2. Extract `LLMRunner` and one small runner, preferably `PromptEnhancer` or `StoryGenerator`, into a backend module.
3. Keep `GeneratorConfig` in place for the first extraction to avoid combining runner extraction with config migration.

Reason:

- `PromptEnhancer` and `StoryGenerator` have fewer filesystem and manifest dependencies than rewrite/translation.
- They still exercise provider/client construction and streaming behavior.

### Riskiest Extraction Target

`TranslationRunner` is riskiest.

Reasons:

- depends on `TranslationConfig`
- depends on `TranslationProfile`, `SegmentParser`, glossary/DNT/protected-regex loading
- emits several preview/output/report queue events
- writes per-segment files and manifests
- optionally invokes validation
- interacts with the shared chunk/segment table

`ChunkedRewriter` is also risky because of legacy `rewrite.py` helper loading and retry-manifest behavior.

### Dependencies Between Runner Classes and UI Widgets

Runners do not directly reference Tk widgets.

They depend on:

- `GeneratorConfig`
- `queue.Queue[tuple[str, Any]]`
- `threading.Event`
- OpenAI-compatible client construction via `providers.py`
- filesystem paths resolved relative to `APP_DIR`
- event names consumed by `StoryGeneratorApp.process_queue()`

UI depends on runner event names and payload shapes.

Important stable event payloads:

- `chunk` and `segment` expect dictionaries compatible with `update_chunk_row()`.
- text preview events expect string payloads.
- `models_list` expects `list[str]`.
- `error` expects a user-visible error string.
- `done` triggers `set_running(False)`.

### Queue/Threading/Event Assumptions

Current worker assumption:

- Only one worker thread runs at a time.
- `start_worker()` clears live preview/log, sets running state, starts a daemon thread, records history run boundaries, catches exceptions, and posts `done`.
- `process_queue()` runs on the Tk thread via `after(100, ...)`.
- `stop_event` is checked by streaming loops and some chunk/segment loops.

Extraction must preserve:

- one worker at a time
- no direct Tk calls from worker threads
- same stop-event semantics
- same fatal error queue behavior
- same `done` event behavior

### Config Dependencies

`GeneratorConfig` is currently the central shared object for:

- provider settings
- routing/cost settings
- story settings
- rewrite settings
- translation settings
- validation settings
- prompt enhancer settings
- history settings

Config split should be designed before broad runner extraction becomes deep refactor work, but the first runner extraction can leave `GeneratorConfig` intact.

### Functions That Must Remain Stable

Important public-ish surfaces to keep stable during the next refactor:

- `load_saved_config()`
- `save_settings()`
- `collect_config()`
- `start_worker()`
- `process_queue()`
- `update_chunk_row()`
- `history_run_paths()`
- `LLMRunner.create_stream_with_retries()`
- `StoryGenerator.run()`
- `ChunkedRewriter.run()`
- `PromptEnhancer.run()`
- `TranslationRunner.preview_segments()`
- `TranslationRunner.run()`
- `TranslationRunner.validate_current_output()`
- provider helpers in `providers.py`
- parser/profile/validator public classes

## 6. Recommended Corrections to PLAN_PASS2_COMPREHENSIVE.md

Make these targeted corrections before using the plan for execution:

1. Update SQLite status.

Current plan says:

`Step 5 - SQLite Implementation: not started / deferred`

Correct to:

`Step 5 - SQLite Implementation: complete at minimal metadata-history level. history_db.py exists, GeneratorConfig includes history_enabled/history_db_file, app_data/workstation_history.sqlite3 is the default DB, and start_worker records run boundaries. History dashboard/UI remains deferred.`

2. Update Step 7.

Current plan treats SQLite as future-if-existing. Correct to:

`Pass 2 Step 7 - SQLite History UI: future UI/dashboard only; the storage layer already exists.`

3. Rename translation extraction target.

Current plan says `TranslationEngine`. Correct to:

`TranslationRunner` unless a deliberate rename is planned.

4. Clarify API key safety.

Correct to:

- primary `api_key` is already masked and blanked on JSON save
- `default_api_key_value` is still saved and should be reviewed/masked/possibly treated as non-secret-only
- history snapshots already redact key/secret/token/password fields

5. Add moved-project sample-path caveat.

The current project does not contain the `01_test translation` sanitized sample folder, while defaults/config still reference it. The plan should separate:

- source-code built-ins are synthetic
- current saved config has stale old absolute paths
- sample folder needs either restoration, path migration, or explicit optional status

6. Clarify provider-events status.

Provider test/model-list runs are recorded in `runs`, but `provider_events` table is not currently populated by `story_generator_ui.py`.

7. Update "Step 2 Provider Polish complete" wording.

Use "mostly complete / mock verified" rather than absolute complete, because local server behavior still needs optional manual testing against real LM Studio/Ollama endpoints.

## 7. Recommended Next Action

Recommended choice: revise `PLAN_PASS2_COMPREHENSIVE.md` first.

Reason:

The plan is directionally good, but it contains a major outdated SQLite assumption and a few smaller naming/safety inaccuracies. Revising the plan before writing `ARCHITECTURE_REFACTOR_PLAN.md` will prevent the next architecture plan from treating already-implemented history behavior as future work.

After that correction, proceed to Architecture Refactor Plan.

## 8. Testing Performed

Command run:

```powershell
python -m py_compile story_generator_ui.py providers.py provider_smoke_tests.py history_db.py history_db_smoke_tests.py segmentation.py translation_profiles.py translation_validator.py rewrite.py "original story deepseek.py"
```

Result: passed.

No cloud APIs were called.

No local LM Studio/Ollama server was required.

No Python source files were modified.

## 9. Files Created

- `PASS2_REALITY_CHECK.md`

