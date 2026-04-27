# Staged Plan: Long Document LLM Workstation UI Refactor

## Current State
- The app is mostly a single Tkinter shell in `story_generator_ui.py`, with backend helpers split into `providers.py`, `segmentation.py`, `translation_profiles.py`, and `translation_validator.py`.
- Backend workflows already exist for story generation, chunked rewrite, prompt enhancement, translation, and validation. They share `GeneratorConfig`, queue-based worker execution, a shared `LLMRunner`, and existing UI queue events.
- Provider/local API support has been added:
  - `ProviderProfile`, provider presets, `build_client`, `chat_completion_kwargs`, model listing, and connection testing live in `providers.py`.
  - OpenRouter `extra_body` is only produced by `openrouter_extra_body()` when `provider_type == "openrouter"`.
  - Local/custom providers use token planning/context warnings instead of cloud cost estimates.
- The main UI pain point is layout density:
  - Left notebook contains many workflow tabs.
  - Right notebook always shows every large surface at once: story prompt, system prompts, rewrite prompt, translation views, validation report, prompt tools, live output, chunks/segments, log.
  - Footer still contains workflow-specific commands: Start Story, Start Rewrite, Retry Chunk, Start Translation, Validate Translation, Open Story/Rewrite/Translation.
  - This makes the app feel like every workflow is active at once.

## Step 1 — UI Refactor Only
**Files likely affected**
- `story_generator_ui.py`
- optionally `UI_README.txt` and `CHANGELOG.md` only if the implementation includes docs/version notes.

**Implementation outline**
- Keep the existing left-side notebook and existing visual style.
- Add `self.controls_notebook = controls` and bind `<<NotebookTabChanged>>` to a new `sync_workspace_for_selected_workflow()` method.
- Replace the single always-full right notebook with a right-side workspace notebook whose tabs are changed based on the selected left workflow.
- Create all existing right-side frames once, keep the same widget attributes, and show/hide them by `Notebook.forget()`/`Notebook.add()` rather than destroying/recreating widgets.
- Use this exact workspace mapping:
  - `Model / Provider`: `Log`
  - `Cloud Routing / Cost`: `Log`
  - `Story Generation`: `Story Prompt`, `System`, `Live Output`, `Log`
  - `Rewrite`: `Rewrite Prompt`, `Live Output`, `Chunks/Segments`, `Log`
  - `Translation`: `Translation Instructions`, `Translation Source Preview`, `Translation Output Preview`, `Chunks/Segments`, `Log`
  - `QA / Validation`: `Validation Report`, `Translation Source Preview`, `Translation Output Preview`, `Chunks/Segments`, `Log`
  - `Prompt Tools`: `Prompt Tools`, `Log`
- Move workflow-specific buttons from the footer into the matching left-side workflow tabs:
  - Story tab: `Start Story`, `Open Story`
  - Rewrite tab: `Start Rewrite`, `Retry Chunk`, `Open Rewrite`
  - Translation tab: `Load Translation Profile`, `Preview Segments`, `Start Translation`, `Open Translation Output`
  - QA / Validation tab: `Validate Translation`, `Open Validation Report`, `Open Translation Output`
  - Prompt Tools tab: leave prompt enhancer buttons in the right-side Prompt Tools workspace; keep enhancer model/temp on the left Prompt Tools tab.
- Footer must contain only:
  - status label
  - `Save Settings`
  - `Stop`
  - progress bar
- Preserve existing button command methods. Only move widget creation and update `set_running()` to enable/disable the new button attributes.
- Keep all queue events and backend classes unchanged.

**Risks**
- Reparenting existing widgets incorrectly can break queue updates if attributes like `self.preview_text`, `self.log_text`, or `self.chunk_tree` are recreated or lost.
- Tkinter notebook tab removal must not destroy frames.
- `set_running()` may reference old footer button attributes unless updated carefully.
- Prompt enhancer text widgets must remain reachable by existing methods.

**Test commands**
- `python -m py_compile story_generator_ui.py providers.py segmentation.py translation_profiles.py translation_validator.py rewrite.py "original story deepseek.py"`
- Tk startup smoke test:
  ```powershell
  @'
  from story_generator_ui import StoryGeneratorApp
  app = StoryGeneratorApp()
  print(app.title())
  app.update_idletasks()
  app.destroy()
  '@ | python -
  ```
- Manual smoke test without API calls:
  - Switch every left tab and confirm the right workspace tabs change.
  - Click `Preview Segments`.
  - Select a chunk/segment row and confirm rewrite retry field still updates for numeric chunk rows.
  - Confirm footer only has global controls.

**Must not be changed**
- No backend workflow behavior.
- No provider logic.
- No prompts/profiles/validator rules.
- No new features.
- No SQLite.

## Step 2 — Provider Polish
**Files likely affected**
- `providers.py`
- `story_generator_ui.py`
- optionally docs/changelog.

**Implementation outline**
- Add focused tests or small non-cloud smoke helpers around provider request construction.
- Verify `chat_completion_kwargs()` includes `extra_body` only for OpenRouter.
- Verify LM Studio/Ollama/custom presets produce dummy/local API key behavior without requiring a real key.
- Verify non-streaming fallback still adapts responses through `response_to_stream_chunks()`.
- Verify local/custom provider estimates display token planning and context warnings, not dollar estimates.
- Verify Test Connection prefers model listing and falls back to a tiny chat test.
- Verify List Models shows a picker only when the endpoint returns model IDs.

**Risks**
- Local providers may vary in exact OpenAI-compatible behavior.
- Some local servers support chat but not `/v1/models`.
- `provider_max_output_tokens` currently clamps requested max tokens; changing this behavior could surprise existing cloud users.

**Test commands**
- `python -m py_compile story_generator_ui.py providers.py`
- Python-only request construction checks using fake config/profile objects; no real API calls.
- Optional manual local tests only when LM Studio/Ollama is running.

**Must not be changed**
- Do not add per-workflow provider selection yet.
- Do not call cloud APIs in automated tests.
- Do not weaken OpenRouter cost/routing behavior.

## Step 3 — Translation/Profile Hardening
**Files likely affected**
- `translation_profiles.py`
- `translation_validator.py`
- sanitized files under `01_test translation`
- optionally docs/changelog.

**Implementation outline**
- Re-scan built-in profiles and non-obsolete sample files for real-looking names, study IDs, WU IDs, work orders, internal paths, and real-looking URLs.
- Keep only approved synthetic placeholders:
  `FICTIVE_CLIENT_ALPHA`, `FICTIVE_LSP`, `SAMPLE_APP`, `STUDY-000-0001`, `WORKUNIT-ALPHA-001`, `WORKORDER-ALPHA-001`, `C:\Users\TestUser\AppData\Local`, `https://example.invalid/study/STUDY-000-0001`.
- Keep validator generic:
  - segment structure checks
  - DNT/protected token preservation
  - placeholder/URL/path/code preservation
  - leaked placeholder and translator note checks
- Do not make validator depend on the sample profile.

**Risks**
- Sanitizing sample files can invalidate expected mock-bad validation counts, though fail/pass behavior should remain.
- Over-broad regexes can create noisy false positives.

**Test commands**
- Identifier scan:
  ```powershell
  Select-String -Path story_generator_ui.py,translation_profiles.py,translation_validator.py,segmentation.py,providers.py,UI_README.txt,CHANGELOG.md -Pattern 'Clinical Trial Media|Lionbridge|Command Desk|TAK-|WU-|WO-000|W443|TestUser\\AppData|example\.com|GZMS|GZMT|STUDY-000-0002'
  ```
- Sample scan excluding obsolete folder.
- Validator smoke test against sanitized source and mock bad output.

**Must not be changed**
- Do not add new translation features.
- Do not introduce real client/project examples.
- Do not remove existing generic profile loading.

## Step 4 — SQLite Design Only
**Files likely affected**
- New design doc only, for example `SQLITE_DESIGN.md`.

**Implementation outline**
- Design metadata/history storage only.
- Keep full documents, chunks, translations, validation reports, and large outputs on disk.
- Proposed DB responsibilities:
  - run history metadata
  - workflow type
  - provider/model snapshot
  - source/output/report file paths
  - token/cost estimates
  - status/error summary
  - timestamps and elapsed time
  - config snapshot hash or selected settings subset
- Preserve JSON config as the active settings mechanism.
- Do not implement database code in this stage.

**Risks**
- Designing too much schema before UI/provider behavior stabilizes can lock in wrong assumptions.
- Storing full documents in DB would bloat and complicate backups.

**Test commands**
- None required beyond reviewing the design doc.

**Must not be changed**
- No SQLite implementation.
- No migrations.
- No runtime DB dependency.
- No config replacement.

## Step 5 — SQLite Implementation Later
**Files likely affected**
- Future small persistence module, likely separate from `story_generator_ui.py`.
- Minimal UI additions only after UI/provider behavior is stable.

**Implementation outline**
- Implement metadata/history after the UI refactor and provider polish are stable.
- Add append-only run records.
- Keep JSON config compatibility.
- Store only paths and metadata, never full documents.

**Risks**
- Adding persistence too early will make current UI refactor harder to verify.
- History UI can easily become another crowded panel if added before workspace refactor is complete.

**Test commands**
- Future unit tests for schema initialization, insert/list run records, and JSON config compatibility.
- Manual smoke tests for old config loading.

**Must not be changed**
- Do not store API keys.
- Do not store full generated or translated documents.
- Do not replace current file-based outputs.

## Recommended Next Single Coding Step
Implement Step 1 only: the right-side workspace refactor plus moving workflow-specific buttons into their workflow tabs, while preserving all existing backend behavior and provider logic.
