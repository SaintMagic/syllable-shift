# SyllableShift — Long Document LLM Workstation

SyllableShift is a Python/Tkinter desktop workstation for long-document LLM workflows. It is built for practical drafting, rewriting, translation, validation, and provider testing with cloud or local OpenAI-compatible APIs.

## Features

- Long-form story generation
- Chunked rewrite workflow
- Prompt enhancement tools
- Batch translation workflow
- Translation QA/validation reports
- OpenRouter and local OpenAI-compatible provider support
- LM Studio and Ollama local-provider presets
- Provider connection and model-list tests
- Cost/token estimates for cloud workflows
- Metadata-only SQLite run history

## Current Status

This project is usable but still under active restructuring. The main app works, but the architecture is being gradually split into smaller workflow, provider, config, and UI modules.

DOCX translation bridge work is planned but paused.

## Quick Start

1. Install Python 3.11+.
2. Install dependencies:

   ```powershell
   pip install openai
   ```

3. Optional for OpenRouter:

   ```powershell
   setx OPENROUTER_API_KEY "your_api_key_here"
   ```

4. Run the app:

   ```powershell
   python story_generator_ui.py
   ```

For local models, start LM Studio or Ollama first, then select the matching provider preset in the app.

## Validation Commands

Run these before making changes:

```powershell
python -m py_compile story_generator_ui.py workflows.py workflow_events.py providers.py provider_smoke_tests.py history_db.py history_db_smoke_tests.py segmentation.py translation_profiles.py translation_validator.py rewrite.py "original story deepseek.py"
python provider_smoke_tests.py
python history_db_smoke_tests.py
```

These checks do not require cloud API calls.

## Privacy / Safety

Do not commit API keys, `.env` files, local config files, generated outputs, SQLite history databases, logs, or private source documents.

Local provider mode sends requests to the configured local endpoint. Verify your LM Studio, Ollama, or custom endpoint settings before using sensitive text.

Cloud provider mode sends requests to the selected external API provider.

## License / Copyright

Copyright © 2026 Martin Baculík. All rights reserved.

This repository is public for visibility and review only. It is not open source.

No license is granted for copying, modifying, distributing, sublicensing, hosting, packaging, selling, or creating derivative works from this code.

Any use requires explicit written permission from the copyright holder.
