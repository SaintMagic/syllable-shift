# Comprehensive Pass 2 Plan: Long Document LLM Workstation

## 1. Executive Summary
**Current Condition:** The application is highly functional, successfully handling complex long-document workflows including story generation, rewriting, translation, and QA validation. The provider backend and token constraints logic are robust. However, the architectural load on `story_generator_ui.py` is unsustainable; sitting at nearly 2,700 lines, it acts as a "god file" that unifies threading, LLM connections, and raw Tkinter UI state. 
**Verdict:** Feature development (especially the DOCX bridge) must pause while the architecture is hardened and refactored.
**Recommended Next Step:** `Pass 2 Step 1 — Architecture Refactor Plan Only`.
**Reason:** We need a concrete mapping of the Model-View-Controller boundaries and migration execution before attempting to physically decouple the components.

---

## 2. `PLAN.md` Completion Review

*   **Step 1 — UI Refactor: complete** (The contextual right-side workspaces tied to the left-side workflow tabs are functional).
*   **Step 2 — Provider Polish: mostly complete / mock verified** (`provider_smoke_tests.py` exists and passes; OpenRouter routing is isolated from local/custom providers. Optional manual testing against real LM Studio/Ollama servers is still needed, and the `provider_events` table exists but provider-event rows are not currently wired/populated).
*   **Step 3 — Translation/Profile Hardening: mostly complete** (Built-in profile identifiers are synthetic and the validator remains generic enough for current use. Caveat: the moved project is missing the `01_test translation` sanitized sample folder, while saved config/default paths may still reference old `C:\Users\TestUser\AppData\Local\SyllableShift\...` locations. Treat this as config/sample cleanup or migration work, not built-in sample leakage).
*   **Step 4 — SQLite Design: complete** (`SQLITE_DESIGN.md` explicitly scopes the DB to metadata and token counting rather than full-document storage).
*   **Step 5 — SQLite Implementation: complete at minimal metadata-history level** (`history_db.py` exists, schema version 1 exists, the default DB path is `app_data/workstation_history.sqlite3`, `GeneratorConfig` includes `history_enabled` / `history_db_file`, and `start_worker()` records run boundaries. The history dashboard/UI remains deferred).

**Conclusion:** Base configurations, basic queue stability, and provider boundaries are verified and should not be aggressively revisited unless broken. The focus must shift directly to runner extraction.

---

## 3. Gemini Review Incorporation
The comprehensive UI/UX review highlighted the fundamental required pivots:
- The **`story_generator_ui.py` god-file risk** is the number one threat to maintainability. Background logic and Tkinter presentation must be explicitly untangled.
- **Workflow extraction**: The `LLMRunner` and its subclasses must move to an isolated backend component (`workflows.py`) operating via strict queue messaging.
- `AppConfig` (global providers, API key) must be severed from `ProjectConfig` (story targets, rewrite chunk sizes) to prevent state overwriting.
- **API Key handling**: The primary `api_key` is already masked in the UI and blanked on JSON save. `default_api_key_value` is still saved and needs review/masking/non-secret-only treatment. History snapshots already redact fields containing `key`, `secret`, `token`, or `password`.
- Legacy `rewrite.py` needs to be formally consumed/deprecated from dynamic imports to standardize operations.
- The UI must bury granular parameters ("Model Tweaks") inside collapsible zones to simplify the UX.

---

## 4. Current Risk Register

### Critical
- **Architecture/God-file**: High probability of regression when touching Tkinter frames due to coupled LLM thread execution. 
  *Mitigation: Extract core runners into headless modules.*
- **Config/Project state mixing**: Saving translation paths overrides previous story generation lengths. 
  *Mitigation: Introduce distinct App vs. Project configuration handling.*
- **API key persistence edge case**: The primary `api_key` is masked and blanked on JSON save, but `default_api_key_value` is still persisted and could become a footgun if a user enters a real secret there.
  *Mitigation: Treat `default_api_key_value` as non-secret-only, consider masking/relabeling it, continue loading real secrets via ENV, and preserve history/config redaction rules.*

### High
- **Hard-to-test workflow classes**: `StoryGenerator` runs can't be unit tested natively without initializing a Tkinter UI instance.
  *Mitigation: Fully headless queue execution for logic wrappers.*
- **Runner logic tied to UI/threading**: Callbacks are risky if notebook frames are destroyed mid-generation.
  *Mitigation: Enforce unidirectional event flows via safely mapped Tk events.*
- **No project/workspace model**: Lack of distinct workspace boundaries confuses navigation.

### Medium
- **Advanced UI clutter**: Makes onboarding difficult for average users.
  *Mitigation: Implement aggressive collapsible frames for Top-P, chunk limits, and Regex bindings.*
- **Validation reports too technical**: Hard to digest quickly.
  *Mitigation: Introduce parsed HTML/prettified output for validation reporting.*
- **Legacy files**: `rewrite.py` fragments development paths.

### Low
- **Visual polish**: General unstyled padding and borders.
- **Formatting/Linting**: Python stylistic divergence.

---

# 5. Second-Pass Implementation Roadmap

## Pass 2 Step 1 — Architecture Refactor Plan Only
**Purpose:**
- Create `ARCHITECTURE_REFACTOR_PLAN.md`.
- Map current responsibilities cleanly.
- Define the module layout state for a separated MVC.
- Plan the specific migration strategy.
- **Do not implement code.**

**Focus:** Where runners will ultimately live, what exact elements stay strictly within the UI, how to handle the configuration decoupling, and strategies to prevent breaking existing Tkinter queues during refactoring.

---

## Pass 2 Step 2 — Extract Core Runners
**Purpose:**
- Move `LLMRunner`, `StoryGenerator`, `ChunkedRewriter`, and `TranslationRunner` directly out of `story_generator_ui.py`.

**Target module:** `core/workflows.py` or `workflows.py`

**Requirements:**
- Ensure all public method behaviors are preserved exactly.
- Guarantee the UI relies on stable, mocked workflow boundaries.
- The underlying application queue / multithreaded messaging remains undisturbed.
- Prove with smoke tests.

---

## Pass 2 Step 3 — Config Split Design
**Purpose:**
- Design `AppConfig` vs `ProjectConfig`.

**`AppConfig` parameters:**
- Provider presets, Base URLs, specific API routing keys/behaviors, global application options, SQLite behaviors.

**`ProjectConfig` parameters:**
- File paths (rewrite input/output), targets (min/max word counts), validation profiles, target language constraints, prompt overrides.

**Requirements:**
- Design a formal migration approach from the legacy `GeneratorConfig`.
- Do not break backward compatibility for older JSON configurations.
- Do not over-engineer a massively heavy save/load system immediately.

---

## Pass 2 Step 4 — API Key Safety
**Purpose:**
- Confirm and tighten API key safety after the current partial safeguards.

**Requirements:**
- Preserve the existing behavior where the primary `api_key` field is visually masked and blanked before JSON config save.
- Review `default_api_key_value`, because it is still saved and should be masked, relabeled as dummy/local-only, or treated as non-secret-only.
- Rely on system environment variables first (`OPENROUTER_API_KEY`, etc.).
- Visually mask the API input within the Tkinter UI (`show="*"`).
- Omit/redact keys from console outputs, `logs`, `history_db` snapshots, and configuration JSON updates explicitly.
- Preserve the existing history snapshot redaction for fields containing `key`, `secret`, `token`, or `password`.
- Allow previous workflow operations to continue as normally expected.

---

## Pass 2 Step 5 — UI Advanced Settings Polish
**Purpose:**
- Radically reduce visual noise in the active application window without resorting to a QT/Electron refactor.

**Candidate items:**
- Deploy accordion menus / collapsible `Model Tweaks`.
- Permanently lock the Provider/Model dropdown array into a dedicated Global Settings or Top Header.
- Leave specific `Prompt Tools` separated on the sidebar.
- Introduce an explicitly targeted "Log Viewing" popout/sliding tab.
- Automatically guess output path names based on inputs.
- Expose basic cost metrics or specify that local mode sends requests to the configured endpoint, and users must verify it, rather than asserting data never leaves the machine.

---

## Pass 2 Step 6 — Project/Workspace Concept
**Purpose:**
- Design the project/workspace concept first.
- Do not implement until AppConfig/ProjectConfig split is approved.

**Requirements:**
- Define project file extensions or schemas dynamically.
- Reference input/output targets consistently by relative or absolute paths.
- Force interactions seamlessly with the baseline `AppConfig` provider settings.

---

## Pass 2 Step 7 — SQLite History UI
**Purpose:**
- Future UI/dashboard work only.
- The SQLite storage layer already exists at the minimal metadata-history level.
- Defer dashboard work until after config/workspace design.

**Requirements:**
- Build a minimal run-history dashboard widget (cost and token accounting).
- Strictly adhere to metadata scoping (Cost, Token limits, Work modes).
- Do not bloat DB reads by storing the long-document contents inside the records.
- Preserve the existing `history_db.py` storage layer and default DB location unless a separate migration plan is approved.

---

## Pass 2 Step 8 — Validation Report Readability
**Purpose:**
- Upgrade translation output evaluation into an easily searchable, human-readable format.

**Ideas:**
- Add collapsed grouped-issue Markdown formatting or straightforward HTML parsing.
- Offer high-level token-loss Severity Summaries.
- Enshrine grouped issues as the primary source of truth (keep the raw diagnostic feed quietly accessible).

---

## Pass 2 Step 9 — Legacy Cleanup
**Purpose:**
- Sever aging components and standard deviation.

**Requirements:**
- Entirely deprecate or encapsulate `rewrite.py`.
- Remove legacy OS dynamic loading loops where appropriate.
- Introduce and document strict `obsolete-DNU` (Do Not Use) handling rules.

---

## Pass 2 Step 10 — Test/Lint Baseline
**Purpose:**
- Secure regression functionality before introducing DOCX parsing or deployment protocols.

**Include:**
- Lightweight `unittest` suites.
- Formal `py_compile` checking limits.
- Validated Provider + Validator specific mock bounds tests.
- Non-breaking UI boot confirmation smoke tests.
- Optional seamless integration of `ruff` tracking.

---

## Pass 2 Step 11 — Documentation Folder
*This is a **future planned step only** in this pass. Do not create these files now.*

**Required future files:**
```text
README.md
docs/00_PROJECT_OVERVIEW.md
docs/01_USER_GUIDE.md
docs/02_WORKFLOWS.md
docs/03_PROVIDERS_LOCAL_AND_CLOUD.md
docs/04_TRANSLATION_QA.md
docs/05_VALIDATION.md
docs/06_ARCHITECTURE.md
docs/07_DEVELOPER_GUIDE.md
docs/08_PRIVACY_AND_DATA_SAFETY.md
docs/09_TROUBLESHOOTING.md
docs/10_ROADMAP.md
CHANGELOG.md
```

**Documentation Rules:**
- Avoid fictionalizing unimplemented capabilities. Indicate experimental flags heavily.
- Utilize only specific synthetic identifiers for trials (`FICTIVE_CLIENT_ALPHA`, `SAMPLE_APP`, `STUDY-000-0001`, `WORKORDER-ALPHA-001`).

---

## Pass 2 Step 12 — Prepare GitHub Repository
*This is a **future planned step only** in this pass. Do not create repo files now.*

**Scope:**
- Align directory layouts, `.gitignore`, and safety verification constraints.
- Verify absolutely zero leakage of generated private client outputs, testing logs, localized secrets, or keys.

**Required future `.gitignore` entries:**
```gitignore
__pycache__/
*.pyc
.venv/
venv/
.env
*.sqlite3
*.db
app_data/
output/
outputs/
rewrite_chunks/
translation_segments/
*.log
story_generator_ui_config.json
*api_key*
obsolete-DNU/
```

---

# 6. Explicitly Out of Scope for Pass 2

- **DOCX bridge implementation.**
- New proprietary document ingestion frameworks (PDF/OCR).
- Total framework shifts (Electron/Qt).
- Injecting live Cloud API requirements into the testing loop.
- Vector searching logic, LLM embedding models, or document library capabilities.
- Actively executing **Step 11 (Documentation)** or **Step 12 (GitHub)** code generation operations right now.

---

# 7. UI Polish Notes From `PLAN_PASS2.md`

- Left panels feel tall/dense; collapsible groups are a future necessity.
- Scale mismatch: some input fields stretch extensively where labels are awkwardly minute.
- Prompt Tools right-side layout requires adjusted vertical proportions.
- Provider monitoring could theoretically adopt a "Status" dashboard view rather than pure textual logs.
- Top-level workflow tabs are technically acceptable but remain functionally crowded.

**Gemini Contextual UI Priority Additions (Pass 1 Targeted Features):**
- Global Provider/Model area extraction.
- Formal "Model Tweaks" collapsible accordion UI component.
- Review remaining API key safety edge cases, especially persisted `default_api_key_value`; primary `api_key` masking already exists.
- Dynamic path creation for missing outputs relative to selected targeted input files.
- Separated Window / Popout for comprehensive logging visibility.

---

# 8. Recommended Next Single Step

**Pass 2 Step 1 — Architecture Refactor Plan Only**

**Reasoning:**
Tackling the technical debt is the ultimate blocker. Defining a comprehensive Model-View separation on paper avoids introducing chaotic widget state errors. By planning the split of the God file purely beforehand, the project drastically limits regressions, lowers bug risk, and securely sets up the eventual project/config extraction steps properly before any raw code lines change.

---

# 9. Test Strategy For Future Passes

- Continuous `python -m py_compile` validations.
- Instantaneous Tkinter visual boot checks.
- Execution coverage using `provider_smoke_tests.py` and strictly mock-based validator trials.
- UI manipulation tests: Ensure configuration load/saves don't corrupt JSON trees or display plain API keys following navigation loops.
- Fallback validations enforcing boot capabilities regardless of Local-Provider offline statuses or SQLite read exceptions.

---

# 10. Implementation Guardrails

*Include these rules in future plans mapped to execution agents:*
- **Do NOT** scrap the repository and restart. Extend iteratively.
- **Preserve** all existing baseline workflows and provider connections.
- **Maintain** mock isolation logic in sampling files.
- **Do NOT** dump raw user tokens/keys into tracking or DB storages.
- **Halt** completely on the DOCX bridge for the time being.
- Implement exactly **one operational step per processing pass.**
- Stop and request review configuration upon completing each respective isolated scope item.
- Do not engage final repository deployments (Documentation/GitHub pushing) unless formally green-lit.
