# Long Document LLM Workstation

Python/Tkinter workstation for long-document LLM workflows:

- story generation
- chunked rewrite
- prompt enhancement
- translation
- translation QA/validation
- OpenRouter and local OpenAI-compatible providers
- metadata-only SQLite run history

This repository is being prepared for safe public/source-control use. Runtime outputs, local config, SQLite history, caches, logs, and secret-like files are intentionally ignored by `.gitignore`.

## Safety Notes

- Do not commit `story_generator_ui_config.json`.
- Do not commit `.env` files or API keys.
- Do not commit generated stories, rewrite chunks, translation segment outputs, SQLite DB files, or logs.
- Built-in examples and profile identifiers should remain synthetic only.
- Local provider mode sends requests to the configured endpoint; verify your local server settings before using sensitive text.

See `REPO_SAFETY_CHECK.md` before initializing or uploading the repository.

