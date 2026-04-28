# Changelog

All notable changes to the Long Document LLM Workstation are tracked here.

This project uses semantic-ish versioning:

- Major version: workflow or config changes that may need user attention.
- Minor version: new features that keep existing saved config working.
- Patch version: fixes, validation tweaks, and small UI improvements.

## [Unreleased]

### Added

- Added extracted workflow runners for clearer story, rewrite, prompt enhancement, translation, and validation execution paths.
- Added config model skeletons and tests for future app/project config separation.
- Added session-only API key status, Enter API Key dialog, Clear API Key action, and a compact Credentials section.

### Changed

- Hardened config safety around API keys and portable paths so raw keys are not saved and local paths are normalized more safely.
- Fixed provider preset binding so built-in presets reliably restore provider defaults.
- Cleaned up Credentials UI copy and local dummy key status for OpenRouter, LM Studio, Ollama, and custom providers.
- Improved dark-theme selection contrast for entries, comboboxes, text areas, and dropdown/list selections.

## [2.1.0] - 2026-04-27

### Added

- Added reusable provider abstraction in `providers.py`.
- Added provider presets for OpenRouter, LM Studio Local, Ollama Local, and Custom OpenAI-Compatible endpoints.
- Added provider capability fields for streaming, structured output, JSON schema, tools, reasoning controls, API key requirements, model listing, context window, and max output tokens.
- Added Test Connection and List Models actions.
- Added local provider mode cost behavior: local/custom providers show token planning and context warnings instead of cloud API cost estimates.

### Changed

- Story generation, rewrite, prompt enhancement, and translation now share the same provider-aware OpenAI-compatible client path.
- OpenRouter routing/cost-cap metadata is only sent for the OpenRouter provider type.
- Split provider/model controls from Cloud Routing / Cost controls in the left-side UI.
- Renamed the app to Long Document LLM Workstation.
- Bumped app version to 2.1.0.

### Security / Data Hygiene

- Sanitized built-in/sample translation profile data to use only synthetic placeholders.
- Updated translation sample defaults to the sanitized bundle.

## [2.0.0] - 2026-04-27

### Added

- Introduced app versioning with `APP_VERSION` in `story_generator_ui.py`.
- Added reusable batch translation workflow.
- Added `TranslationConfig`, `TranslationRunner`, `TranslationProfile`, `SegmentParser`, `TranslationValidator`, `ValidationIssue`, and `ValidationReport`.
- Added translation UI controls for input/output files, source and target language, register mode, instruction/profile file, glossary CSV, DNT terms, protected regexes, delimiter style, segment chunking, temperature/top_p, pause timing, and validate-after-run.
- Added QA/Validation UI controls for source file, translated output, validation profile, report file, grouped report mode, and optional JSON report.
- Added right-side tabs for Translation Instructions, Translation Source Preview, Translation Output Preview, Validation Report, and combined Chunks/Segments status.
- Added generic segmentation support for percent segment blocks, Markdown headings, blank-line blocks, whole-file translation, and custom delimiter regexes.
- Added built-in sample translation profile: Clinical/Localization Protected Segment Test.
- Added glossary CSV support with `source_term,target_term,context,note`.
- Added DNT/protected term handling through external files.
- Added translation cost estimate with recharge-adjusted cost.
- Added per-segment/per-chunk translation progress table.

### Changed

- Renamed the app title to Novel + Translation QA Control Panel.
- Reorganized the left-side UI into clearer functional groups: Model / Provider, Story Generation, Rewrite, Translation, and QA / Validation.
- Updated the README to document translation and validation workflows.

### Compatibility

- Existing saved config remains backward-compatible; new fields use defaults when missing.
- API keys are still not saved to config.

## [1.2.0] - 2026-04-26

### Added

- Added chunked rewrite workflow with per-chunk source and rewritten files.
- Added rewrite manifest checks to prevent unsafe retry after source/chunk-size changes.
- Added Prompt Tools tab with prompt enhancement, shortening, stricter prompt generation, and variable extraction.
- Added story target word count controls.
- Added rewrite target ratio controls and desired output estimates.
- Added model presets and cost estimate display with recharge overhead.

### Changed

- Improved numeric validation with bounded sliders/spinboxes.
- Added visible fatal error popups while keeping detailed log output.

## [1.0.0] - 2026-04-26

### Added

- Initial Tkinter UI wrapper for the original OpenRouter story generation script.
- Added editable model, routing, price cap, generation, prompt, continuation, retry, and timeout settings.
- Added live streaming output and run log tabs.
- Added settings save/load while keeping API key out of saved config.
