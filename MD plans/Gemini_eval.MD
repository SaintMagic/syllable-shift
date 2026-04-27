# Project Review: Long Document LLM Workstation

## Executive Summary
This project is an ambitious and highly functional LLM workstation designed for long-document tasks like story generation, translation, and rewriting. The core text-processing logic (segmentation, translation validation, history tracking) is well-thought-out and increasingly separated into distinct modules. However, the application is suffering from "god-object" syndrome on the UI side, with a massive `story_generator_ui.py` file attempting to manage threading, LLM connections, business logic, and Tkinter UI state all at once. Before piling on more features (like the DOCX bridge), the project urgently needs a refactor to separate backend execution logic from the UI layer, and to distinguish global application settings from workspace-specific project data. 

## Critical Issues
1. **The "God File" Risk**: `story_generator_ui.py` is nearly 2700 lines long. It heavily mixes LLM runner logic (`StoryGenerator`, `ChunkedRewriter`, `TranslationEngine`) with Tkinter UI code and threading. This makes debugging, modifying, or testing the core workflows incredibly fragile.
2. **Project vs. App State**: `GeneratorConfig` mashes together global application settings (API keys, provider URLs) and localized project settings (story prompts, chunk limits, input/output files). There is no "workspace" concept, meaning users overwrite their config every time they switch projects.
3. **Plain Text Secrets**: While `history_db.py` smartly redacts API keys before saving to SQLite, the primary config (`story_generator_ui_config.json`) will likely store `api_key` in plain text on disk if the user types it in. This is a security risk.
4. **False Confidence in DOCX Plan**: The proposed DOCX bridge plan (stuffing translated text into the first `<w:t>` node and erasing the rest) will destroy all inline formatting (bold, italics, links) inside translated paragraphs. This will frustrate users who expect rich text preservation. 

## High-Priority Improvements
1. **Extract Background Runners**: Move `LLMRunner`, `StoryGenerator`, `ChunkedRewriter`, and `TranslationEngine` out of `story_generator_ui.py` into a new `runners.py` or `workflows.py` module. The UI should only pass config data to these runners and listen to a queue.
2. **Settings Isolation**: Split the configuration into `AppConfig` (providers, endpoints, API keys, SQLite paths) and `ProjectConfig` (specific story prompts, translation target files, chunk sizes). This naturally leads to a "Save/Load Project" feature.
3. **API Key Handling**: Do not write the API key to `story_generator_ui_config.json`. Store it in the OS Keychain, load it purely from `.env` or environment variables, or at least encrypt it locally.
4. **Kill Legacy Files**: `rewrite.py` appears to be a legacy standalone script partially loaded dynamically via `importlib`. It should be fully deprecated, with its prompt constants and logic fully absorbed into the new runner architecture.

## Medium-Priority Improvements
1. **Translation Test Harness Constraints**: The validation profiles heavily lean toward a specific clinical trial template (`STUDY...`, `eCOA`, etc.). Generalize the built-in profiles so that independent authors/users don't see medical localization logic unless they opt into a `Clinical` preset.
2. **UI Simplification**: The UI is likely overwhelming for non-developers, dropping them directly into OpenRouter token limits and context window configurations. Implement an "Advanced Settings" toggle to hide parameters like `top_p`, `rewrite_max_ratio`, and schema settings behind an accordion or secondary window.
3. **Refine Local Provider Messaging**: "Local mode" messaging in the app should explicitly state "Data never leaves this machine." when LM Studio or Ollama is selected, guaranteeing privacy, while explicitly warning when OpenRouter is used.

## Low-Priority Polish
1. **SQLite Implementation**: The `history_db.py` schema is excellent and perfectly scoped to metadata-only. Finish wiring this into the UI so users have a "Run History" dashboard to estimate cost and token usage over time.
2. **Validation Reporting**: The validation report format is highly technical. Consider a simple HTML export with highlighted text for the user to quickly review localized issues rather than reading raw text logs.
3. **Code Formatting**: Setup `ruff` or `black` to enforce standard line breaks and imports across the board, establishing an iron-clad stylistic baseline.

## Specific Recommended Next Steps
1. **Pause feature development.** Do not build the DOCX bridge yet.
2. Create `core/workflows.py` and migrate the `LLMRunner` inherited classes out of `story_generator_ui.py`. 
3. Introduce `ProjectManager` to separate the monolithic config JSON into global configs and project setups.
4. Implement basic unit tests for the newly isolated workflow runners.
5. Finish wiring up the SQLite history view.
6. Package as a portable executable with an explicit project directory logic. 

## Files/Modules that should be split or refactored
- `story_generator_ui.py` -> Needs to be blown up into `app.py` (main entry point), `ui/main_window.py`, `ui/settings_tab.py`, `core/runners.py`, `core/config.py`.
- `rewrite.py` -> Delete/Deprecate. Fold remaining uniqueness into `core/runners.py`.
- `providers.py` -> Already good, keep isolated.
- `translation_profiles.py` -> Move test-specific clinical mock data into an explicit testing directory or JSON dummy data.

## UI Refactoring Plan (Pass 1)
Following the initial review, a safe, high-ROI UI polish pass has been established. The objective is to declutter the interface without destabilizing the current backend architecture. The following 5 steps will be implemented as Pass 1:
1. **Global Provider/Model Bar & Masked API Key:** Move provider config out of the workflow tabs into a permanent global space. Mask the API key input and avoid saving it to plain-text configuration.
2. **Collapsible Model Tweaks:** Group advanced limits (`temperature`, `top_p`, token limits) into a collapsible "Model Tweaks" frame to clean up the primary view.
3. **Auto Output Path Suggestions:** Automatically pre-fill default output paths based on selected input files via Tkinter traces to reduce user friction.
4. **Log Popout Button:** Implement an external or pop-out window for raw application logs, freeing up the main workspace for text generation/workflow progress.
5. **Standalone Prompt Tools:** Retain Prompt Tools as a separate, generalized workflow to ensure it remains useful for translation instructions and standard generation alike.

The more complex architectural UI changes (e.g., eliminating nested notebooks, full data-binding separation) are deferred to Pass 2, post-backend refactoring.

## Final Verdict
**PAUSE AND REFACTOR** 
The project has excellent DNA, well-reasoned document processing, and a strong understanding of LLM chunking/translation nuance. However, the architectural foundation of the UI is buckling under the weight of its own features. Moving forward with the DOCX bridge will only pile on more technical debt into a 2,600-line god script. Restructure the project around a decoupled Model-View-Controller architecture, implement workspace separation, and *then* proceed with the DOCX bridge and scaling.
