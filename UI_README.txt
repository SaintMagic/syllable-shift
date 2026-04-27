Long Document LLM Workstation
=============================

Current version: 2.1.0

Open "Run Story Generator UI.bat" to start the desktop app.

Release history is tracked in CHANGELOG.md.

The UI lets you edit:
- output file
- provider preset/type, model preset, model ID, and OpenAI-compatible base URL
- API key environment variable or a temporary API key for the current run
- safe-routing provider settings and price caps
- provider capability flags, context window, and max output tokens
- temperature, top_p, max tokens, continuation count, retry count, timeout using sliders and spinboxes
- target story word count, injected into each story run as a length override
- continue marker
- main story prompt
- first-call and continuation system prompts
- prompt enhancement tools with separate source/enhanced boxes
- chunked rewrite input/output/cleaned files
- chunk size, rewrite temperature, rewrite top_p, per-chunk token limit, pause time, target ratios, and ratio warnings
- rewrite system prompt
- batch translation input/output/segment files
- source language, target language, register mode, instruction/profile file, glossary CSV, DNT terms, protected regexes
- delimiter style, segments per API call, translation temperature/top_p, validation-after-run
- validation profile, grouped/raw report mode, report output, optional JSON report

Provider setup
--------------

Use the Model / Provider tab to choose a provider preset, edit the base URL,
set the model ID, and test/list models. Presets only fill fields; you can edit
everything afterward.

OpenRouter:
- Preset: OpenRouter
- Base URL: https://openrouter.ai/api/v1
- API key env: OPENROUTER_API_KEY
- Cloud Routing / Cost controls are enabled only for this provider type.

LM Studio Local:
- Preset: LM Studio Local
- Default base URL: http://localhost:1234/v1
- Default dummy key: lm-studio
- Requires a running LM Studio local server and a loaded model.
- LM Studio documents OpenAI-compatible /v1/models and /v1/chat/completions endpoints:
  https://lmstudio.ai/docs/developer/openai-compat

Ollama Local:
- Preset: Ollama Local
- Default base URL: http://localhost:11434/v1
- Default dummy key: ollama
- Requires a running Ollama server and an installed model whose name matches the Model field.
- Ollama documents OpenAI-compatible /v1/chat/completions and ignored dummy API keys:
  https://docs.ollama.com/api/openai-compatibility

Custom OpenAI-Compatible:
- Preset: Custom OpenAI-Compatible
- Edit base URL, key/env behavior, model ID, and capability flags for your endpoint.

Local endpoints depend on your local server and model settings. The app only says
where requests are sent; it does not make a privacy guarantee about the local server.

The Cloud Routing / Cost tab shows routing status, desired output words, rough
target-based cost estimates, and the same estimates with a 1.28x recharge overhead
when OpenRouter is active. For local/custom providers, API cost is not estimated;
the app keeps token planning and context-window warnings.

If no rewrite input file exists yet, the rewrite estimate will ask you to select
an input file before showing chunk count and desired output words.

The Prompt Tools tab can enhance the story prompt, enhance the rewrite prompt,
make a prompt shorter, make it stricter, or extract editable variables. It never
overwrites your real prompt automatically. Use "Apply to Story" or "Apply to
Rewrite" after reviewing the enhanced prompt. "Restore Previous" restores the
last prompt replaced by an Apply action. It only calls the API when you click
"Enhance Prompt". The enhancer uses the same routing price caps as the main run;
model presets keep the enhancer model aligned with the main model unless you edit
it manually.

The Rewrite mode saves separate files in the chunks folder:
- source_001.md, source_002.md, etc.
- rewritten_001.md, rewritten_002.md, etc.
- manifest.json, used to make sure retrying a chunk still matches the same input,
  cleaned text, chunk size, and chunk count

Use "Retry Chunk" with the selected chunk number to rerun one rewritten chunk and rebuild the combined output file.
Clicking a row in the Chunks/Segments tab also loads that row number into the retry control when it is a rewrite chunk.

Translation mode is generic. The bundled "Clinical/Localization Protected Segment Test"
profile uses sanitized synthetic files in "01_test translation" as sample defaults, but the workflow can use
other Markdown/JSON instruction profiles, glossary.csv files, DNT term lists, protected
regex files, and delimiter styles. "Preview Segments" parses the input without calling the
API. "Start Translation" streams translated chunks to the output file and per-chunk files.
"Validate Translation" runs the reusable QA engine and writes the selected report file.

Bundled samples use synthetic placeholders only, such as FICTIVE_CLIENT_ALPHA,
FICTIVE_LSP, SAMPLE_APP, STUDY-000-0001, WORKUNIT-ALPHA-001,
WORKORDER-ALPHA-001, C:\Users\TestUser\AppData\Local, and example.invalid.
Do not replace them with real client, study, work-order, URL, or path examples
when testing cloud providers.

Validation catches structural and protected-token issues. It does not judge
linguistic quality, fluency, register, or whether the translation is actually good.

Supported glossary CSV columns:
source_term,target_term,context,note

If target_term is empty or [TARGET TERM], the source term is treated as protected/DNT.

Settings are saved to story_generator_ui_config.json. The API key field is intentionally not saved.

If the app says the OpenAI package is missing, run:
pip install openai
