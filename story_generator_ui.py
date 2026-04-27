from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import math
import os
import queue
import re
import threading
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any, Callable

from providers import (
    PROVIDER_PRESETS,
    PROVIDER_TYPES,
    apply_provider_preset_values,
    list_models,
    provider_from_config,
    test_connection,
)
from history_db import HistoryDB, resolve_history_db_path
from segmentation import Segment, SegmentParser
from translation_profiles import (
    GlossaryTerm,
    TranslationProfile,
    builtin_translation_profiles,
    load_glossary,
    load_line_list,
    load_translation_profile,
)
from translation_validator import ValidationReport, TranslationValidator
from workflows import LLMRunner, PromptEnhancer

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore[assignment]


APP_DIR = Path(__file__).resolve().parent
APP_NAME = "Long Document LLM Workstation"
APP_VERSION = "2.1.0"
CONFIG_FILE = APP_DIR / "story_generator_ui_config.json"
ORIGINAL_SCRIPT = APP_DIR / "original story deepseek.py"
REWRITE_SCRIPT = APP_DIR / "rewrite.py"
RECHARGE_OVERHEAD = 1.28

FLOAT_FIELDS = {
    "max_prompt_price",
    "max_completion_price",
    "temperature",
    "top_p",
    "rewrite_temperature",
    "rewrite_top_p",
    "rewrite_min_ratio",
    "rewrite_max_ratio",
    "rewrite_target_min_ratio",
    "rewrite_target_max_ratio",
    "enhancer_temperature",
    "translation_temperature",
    "translation_top_p",
}
INT_FIELDS = {
    "story_target_min_words",
    "story_target_max_words",
    "max_tokens_per_call",
    "max_continuations",
    "max_retries",
    "timeout_seconds",
    "context_window_tokens",
    "provider_max_output_tokens",
    "rewrite_chunk_words",
    "rewrite_max_tokens_per_call",
    "rewrite_pause_seconds",
    "rewrite_selected_chunk",
    "translation_chunk_segments",
    "translation_max_tokens_per_call",
    "translation_pause_seconds",
}
BOOL_FIELDS = {
    "history_enabled",
    "safe_routing",
    "allow_fallbacks",
    "supports_streaming",
    "supports_json_schema",
    "supports_response_format",
    "supports_tools",
    "supports_reasoning_effort",
    "requires_api_key",
    "supports_model_listing",
    "translation_validate_after_run",
    "translation_grouped_report",
    "translation_save_json_report",
}

MODEL_PRESETS = {
    "DeepSeek V4 Flash cheap": {
        "model": "deepseek/deepseek-v4-flash",
        "prompt": 0.14,
        "completion": 0.28,
        "temperature": 0.78,
        "top_p": 0.92,
    },
    "DeepSeek V4 Pro": {
        "model": "deepseek/deepseek-v4",
        "prompt": 0.50,
        "completion": 1.50,
        "temperature": 0.72,
        "top_p": 0.90,
    },
    "Qwen coder": {
        "model": "qwen/qwen3-coder",
        "prompt": 0.30,
        "completion": 1.20,
        "temperature": 0.55,
        "top_p": 0.88,
    },
    "Custom": {},
}


def read_python_constant(path: Path, name: str, fallback: str) -> str:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return fallback

    for node in tree.body:
        if isinstance(node, ast.Assign):
            if any(isinstance(target, ast.Name) and target.id == name for target in node.targets):
                try:
                    value = ast.literal_eval(node.value)
                except Exception:
                    return fallback
                return value if isinstance(value, str) else fallback
    return fallback


DEFAULT_STORY_PROMPT = read_python_constant(
    ORIGINAL_SCRIPT,
    "STORY_PROMPT",
    "Write a completely original long-form sci-fi horror novella from scratch.",
)


def load_rewrite_backend() -> Any | None:
    if not REWRITE_SCRIPT.exists():
        return None
    try:
        spec = importlib.util.spec_from_file_location("rewrite_backend", REWRITE_SCRIPT)
        if spec is None or spec.loader is None:
            return None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


REWRITE_BACKEND = load_rewrite_backend()
DEFAULT_REWRITE_PROMPT = getattr(
    REWRITE_BACKEND,
    "SYSTEM_PROMPT",
    "Rewrite the provided story chunk into polished prose while preserving plot, tone, and continuity.",
)
TRANSLATION_SAMPLE_DIR = APP_DIR / "01_test translation" / "translation_stress_test_v9_sanitized_bundle"
DEFAULT_TRANSLATION_INPUT = TRANSLATION_SAMPLE_DIR / "translation_test_source_segments_v9_sanitized.txt"
DEFAULT_TRANSLATION_INSTRUCTIONS = TRANSLATION_SAMPLE_DIR / "translation_test_instructions_v9_sanitized.md"


@dataclass
class GeneratorConfig:
    output_file: str = "deepseek_original_novella.md"
    history_enabled: bool = True
    history_db_file: str = "app_data/workstation_history.sqlite3"
    provider_preset: str = "OpenRouter"
    provider_name: str = "OpenRouter"
    provider_type: str = "openrouter"
    model_preset: str = "DeepSeek V4 Flash cheap"
    model: str = "deepseek/deepseek-v4-flash"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key_env: str = "OPENROUTER_API_KEY"
    api_key: str = ""
    supports_streaming: bool = True
    supports_json_schema: bool = False
    supports_response_format: bool = False
    supports_tools: bool = False
    supports_reasoning_effort: bool = False
    requires_api_key: bool = True
    default_api_key_value: str = ""
    context_window_tokens: int = 131072
    provider_max_output_tokens: int = 120000
    supports_model_listing: bool = True
    provider_notes: str = "Cloud OpenAI-compatible endpoint with OpenRouter routing/cost controls."
    safe_routing: bool = True
    provider_sort: str = "price"
    allow_fallbacks: bool = False
    max_prompt_price: float = 0.14
    max_completion_price: float = 0.28
    temperature: float = 0.78
    top_p: float = 0.92
    max_tokens_per_call: int = 120000
    max_continuations: int = 5
    continue_marker: str = "[STORY_CONTINUES]"
    max_retries: int = 8
    timeout_seconds: int = 7200
    system_prompt: str = "You are a careful long-form literary horror writer. Output only polished story prose."
    continuation_system_prompt: str = "You are continuing the same original novella. Output only polished story prose."
    story_target_min_words: int = 25000
    story_target_max_words: int = 40000
    story_prompt: str = DEFAULT_STORY_PROMPT
    rewrite_input_file: str = "novel.md"
    rewrite_output_file: str = "novel_rewritten.md"
    rewrite_cleaned_file: str = "novel_cleaned_input.md"
    rewrite_chunks_dir: str = "rewrite_chunks"
    rewrite_chunk_words: int = 1200
    rewrite_temperature: float = 0.65
    rewrite_top_p: float = 0.90
    rewrite_max_tokens_per_call: int = 8000
    rewrite_pause_seconds: int = 10
    rewrite_min_ratio: float = 0.85
    rewrite_max_ratio: float = 1.30
    rewrite_target_min_ratio: float = 0.90
    rewrite_target_max_ratio: float = 1.10
    rewrite_selected_chunk: int = 1
    rewrite_system_prompt: str = DEFAULT_REWRITE_PROMPT
    enhancer_model: str = "deepseek/deepseek-v4-flash"
    enhancer_temperature: float = 0.25
    translation_input_file: str = str(DEFAULT_TRANSLATION_INPUT)
    translation_output_file: str = "translation_output.md"
    translation_segments_dir: str = "translation_segments"
    translation_source_language: str = "English"
    translation_target_language: str = ""
    translation_register_mode: str = "Professional/staff-facing"
    translation_instruction_file: str = str(DEFAULT_TRANSLATION_INSTRUCTIONS)
    translation_instruction_text: str = ""
    translation_glossary_file: str = ""
    translation_dnt_file: str = ""
    translation_protected_regex_file: str = ""
    translation_segment_delimiter_style: str = "Percent Segment Blocks"
    translation_custom_delimiter_regex: str = ""
    translation_chunk_segments: int = 1
    translation_max_tokens_per_call: int = 16000
    translation_temperature: float = 0.20
    translation_top_p: float = 0.90
    translation_pause_seconds: int = 2
    translation_validate_after_run: bool = True
    translation_validator_profile: str = "Clinical/Localization Protected Segment Test"
    translation_validation_report_file: str = "translation_validation_report.md"
    translation_grouped_report: bool = True
    translation_save_json_report: bool = False


@dataclass
class TranslationConfig:
    input_file: Path
    output_file: Path
    segments_dir: Path
    source_language: str
    target_language: str
    register_mode: str
    instruction_file: Path | None
    glossary_file: Path | None
    dnt_file: Path | None
    protected_regex_file: Path | None
    segment_delimiter_style: str
    custom_delimiter_regex: str
    chunk_segments: int
    max_tokens_per_call: int
    temperature: float
    top_p: float
    pause_seconds: int
    validate_after_run: bool
    validator_profile: str
    validation_report_file: Path
    grouped_report: bool
    save_json_report: bool


def resolve_path(value: str, default_name: str) -> Path:
    path = Path(str(value).strip() or default_name)
    return path if path.is_absolute() else APP_DIR / path


def resolve_optional_path(value: str) -> Path | None:
    clean = str(value).strip()
    if not clean:
        return None
    path = Path(clean)
    return path if path.is_absolute() else APP_DIR / path


def load_saved_config() -> GeneratorConfig:
    if not CONFIG_FILE.exists():
        return GeneratorConfig()
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return GeneratorConfig()

    defaults = asdict(GeneratorConfig())
    defaults.update({key: value for key, value in data.items() if key in defaults})
    return GeneratorConfig(**defaults)


def estimate_tokens(text: str) -> int:
    return max(1, math.ceil(len(text) / 4))


def estimate_tokens_from_words(words: int) -> int:
    return max(1, math.ceil(words * 1.35))


def money(prompt_tokens: int, completion_tokens: int, config: GeneratorConfig) -> tuple[float, float]:
    base = (
        prompt_tokens / 1_000_000 * config.max_prompt_price
        + completion_tokens / 1_000_000 * config.max_completion_price
    )
    return base, base * RECHARGE_OVERHEAD


def preclean_text(text: str) -> str:
    if REWRITE_BACKEND and hasattr(REWRITE_BACKEND, "preclean_text"):
        return REWRITE_BACKEND.preclean_text(text)

    lines = text.splitlines()
    garbage_patterns = [
        r"^\s*DEBUG INFO\s*$",
        r"^\s*Thought for .* seconds\s*$",
        r"^\s*continue\s*$",
        r"^\s*continuing in next response\s*$",
        r"^\s*\[CONTINUE FROM HERE\]\s*$",
    ]
    cleaned = [
        line for line in lines
        if not any(re.match(pattern, line, flags=re.IGNORECASE) for pattern in garbage_patterns)
    ]
    return re.sub(r"\n{4,}", "\n\n\n", "\n".join(cleaned)).strip() + "\n"


def split_into_word_chunks(text: str, max_words: int) -> list[str]:
    if REWRITE_BACKEND and hasattr(REWRITE_BACKEND, "split_into_word_chunks"):
        return REWRITE_BACKEND.split_into_word_chunks(text, max_words=max_words)

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_words = 0
    for paragraph in paragraphs:
        words = len(paragraph.split())
        if current and current_words + words > max_words:
            chunks.append("\n\n".join(current))
            current = [paragraph]
            current_words = words
        else:
            current.append(paragraph)
            current_words += words
    if current:
        chunks.append("\n\n".join(current))
    return chunks


class StoryGenerator(LLMRunner):
    def stream_call(self, client: OpenAI, messages: list[dict[str, str]], output_file: Path, append: bool) -> tuple[str, str | None]:
        mode = "a" if append else "w"
        text_parts: list[str] = []
        output_chars = 0
        last_progress = 0
        start_time = time.time()
        finish_reason = None

        output_file.parent.mkdir(parents=True, exist_ok=True)
        with output_file.open(mode, encoding="utf-8", newline="\n") as out:
            stream = self.create_stream_with_retries(
                client,
                messages,
                self.config.temperature,
                self.config.top_p,
                self.config.max_tokens_per_call,
            )
            for chunk in stream:
                if self.stop_event.is_set():
                    raise KeyboardInterrupt
                if not chunk.choices:
                    continue

                choice = chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta.content or ""
                if not delta:
                    continue

                text_parts.append(delta)
                out.write(delta)
                out.flush()
                self.preview(delta)

                output_chars += len(delta)
                if output_chars - last_progress >= 5000:
                    last_progress = output_chars
                    elapsed = time.time() - start_time
                    self.log(f"Written this call: {output_chars:,} chars ({elapsed / 60:.1f} min)")

        return "".join(text_parts), finish_reason

    def run(self) -> None:
        output_file = resolve_path(self.config.output_file, "deepseek_original_novella.md")
        client = self.client()

        self.status("Generating story...")
        self.log(f"Model: {self.config.model}")
        self.log(f"Output file: {output_file}")
        self.log(f"Safe routing: {self.config.safe_routing}")

        story_prompt = (
            self.config.story_prompt
            + "\n\nLENGTH OVERRIDE\n"
            + (
                f"Target final story length: {self.config.story_target_min_words:,} "
                f"to {self.config.story_target_max_words:,} words.\n"
            )
            + "Do not stop early unless you hit a clean continuation boundary."
        )

        messages = [
            {"role": "system", "content": self.config.system_prompt},
            {"role": "user", "content": story_prompt},
        ]

        total_start = time.time()
        append = False
        for call_number in range(1, self.config.max_continuations + 2):
            if self.stop_event.is_set():
                raise KeyboardInterrupt

            self.log(f"Call {call_number}/{self.config.max_continuations + 1}")
            generated, finish_reason = self.stream_call(client, messages, output_file, append=append)
            self.log(f"Call {call_number} finish reason: {finish_reason}")

            full_output = output_file.read_text(encoding="utf-8") if output_file.exists() else ""
            self.log(f"Current output words: {len(full_output.split()):,}")

            needs_continue = self.config.continue_marker in generated or finish_reason == "length"
            if not needs_continue:
                break

            output_file.write_text(
                full_output.replace(self.config.continue_marker, "").rstrip() + "\n",
                encoding="utf-8",
                newline="\n",
            )
            full_output = output_file.read_text(encoding="utf-8")
            if call_number > self.config.max_continuations:
                self.log("Reached maximum continuations. Partial story saved.")
                break

            messages = [
                {"role": "system", "content": self.config.continuation_system_prompt},
                {
                    "role": "user",
                    "content": (
                        "Continue the story from the exact point where it stopped.\n"
                        "Do not restart. Do not summarize. Do not explain.\n"
                        "Preserve the same second-person present-tense style, tone, continuity, headings, and pacing.\n"
                        "Continue from the last sentence of the story below.\n"
                        "If you still cannot finish, stop at a clean section boundary and write exactly:\n"
                        f"{self.config.continue_marker}\n\n"
                        "STORY_SO_FAR_START\n"
                        f"{full_output}\n"
                        "STORY_SO_FAR_END"
                    ),
                },
            ]
            append = True
            with output_file.open("a", encoding="utf-8", newline="\n") as out:
                out.write("\n\n")

        final_text = output_file.read_text(encoding="utf-8")
        elapsed = time.time() - total_start
        self.log("Done.")
        self.log(f"Saved to: {output_file}")
        self.log(f"Output words: {len(final_text.split()):,}")
        self.log(f"Output chars: {len(final_text):,}")
        self.log(f"Total time: {elapsed / 60:.1f} minutes")
        self.status("Done")


class ChunkedRewriter(LLMRunner):
    def paths(self) -> tuple[Path, Path, Path, Path]:
        return (
            resolve_path(self.config.rewrite_input_file, "novel.md"),
            resolve_path(self.config.rewrite_output_file, "novel_rewritten.md"),
            resolve_path(self.config.rewrite_cleaned_file, "novel_cleaned_input.md"),
            resolve_path(self.config.rewrite_chunks_dir, "rewrite_chunks"),
        )

    def build_manifest(self, input_file: Path, cleaned_file: Path, cleaned_text: str, chunk_count: int) -> dict[str, Any]:
        return {
            "input_file": str(input_file.resolve()),
            "cleaned_file": str(cleaned_file.resolve()),
            "cleaned_sha256": hashlib.sha256(cleaned_text.encode("utf-8")).hexdigest(),
            "chunk_size": self.config.rewrite_chunk_words,
            "chunk_count": chunk_count,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

    def validate_retry_manifest(self, chunks_dir: Path, manifest: dict[str, Any]) -> None:
        manifest_file = chunks_dir / "manifest.json"
        if not manifest_file.exists():
            raise RuntimeError(
                "Cannot safely retry a chunk because rewrite_chunks/manifest.json is missing. "
                "Run a full rewrite first."
            )

        previous = json.loads(manifest_file.read_text(encoding="utf-8"))
        mismatches = []
        for key in ("input_file", "cleaned_file", "cleaned_sha256", "chunk_size", "chunk_count"):
            if previous.get(key) != manifest.get(key):
                mismatches.append(key)

        if mismatches:
            raise RuntimeError(
                "Cannot safely retry this chunk because rewrite settings/source changed "
                f"since the chunk files were created. Mismatched: {', '.join(mismatches)}. "
                "Run Start Rewrite to regenerate chunks with the new settings."
            )

    def prepare_chunks(self, clear_outputs: bool) -> list[str]:
        input_file, _, cleaned_file, chunks_dir = self.paths()
        if not input_file.exists():
            raise FileNotFoundError(f"Input file not found: {input_file}")

        raw_text = input_file.read_text(encoding="utf-8")
        cleaned_text = preclean_text(raw_text)

        chunks = split_into_word_chunks(cleaned_text, self.config.rewrite_chunk_words)
        chunks_dir.mkdir(parents=True, exist_ok=True)
        manifest = self.build_manifest(input_file, cleaned_file, cleaned_text, len(chunks))
        if clear_outputs:
            (chunks_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        else:
            self.validate_retry_manifest(chunks_dir, manifest)

        cleaned_file.parent.mkdir(parents=True, exist_ok=True)
        cleaned_file.write_text(cleaned_text, encoding="utf-8", newline="\n")

        if clear_outputs:
            for pattern in ("source_*.md", "rewritten_*.md"):
                for old_file in chunks_dir.glob(pattern):
                    old_file.unlink(missing_ok=True)

        for index, chunk_text in enumerate(chunks, start=1):
            (chunks_dir / f"source_{index:03}.md").write_text(chunk_text, encoding="utf-8", newline="\n")
            self.ui_queue.put((
                "chunk",
                {
                    "index": index,
                    "input": len(chunk_text.split()),
                    "output": "",
                    "ratio": "",
                    "status": "Queued",
                },
            ))

        self.log(f"Loaded: {input_file}")
        self.log(f"Raw words: {len(raw_text.split()):,}")
        self.log(f"Cleaned words: {len(cleaned_text.split()):,}")
        self.log(f"Cleaned input saved to: {cleaned_file}")
        self.log(f"Chunks: {len(chunks)}")
        return chunks

    def rebuild_output(self, total_chunks: int) -> None:
        _, output_file, _, chunks_dir = self.paths()
        parts: list[str] = []
        for index in range(1, total_chunks + 1):
            chunk_file = chunks_dir / f"rewritten_{index:03}.md"
            if chunk_file.exists():
                parts.append(chunk_file.read_text(encoding="utf-8").strip())
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text("\n\n".join(part for part in parts if part) + "\n", encoding="utf-8", newline="\n")

    def rewrite_chunk(self, client: OpenAI, chunk_text: str, index: int, total_chunks: int) -> tuple[int, float, str | None]:
        input_words = len(chunk_text.split())
        min_words = int(input_words * self.config.rewrite_target_min_ratio)
        max_words = int(input_words * self.config.rewrite_target_max_ratio)
        _, _, _, chunks_dir = self.paths()
        rewritten_file = chunks_dir / f"rewritten_{index:03}.md"

        messages = [
            {"role": "system", "content": self.config.rewrite_system_prompt},
            {
                "role": "user",
                "content": (
                    f"You are rewriting chunk {index} of {total_chunks} from a longer story.\n"
                    f"This chunk has approximately {input_words} words.\n"
                    f"Target rewritten length: {min_words} to {max_words} words.\n"
                    "Output below the minimum target length is considered a failed rewrite.\n"
                    "Continue developing the prose until the rewritten chunk reaches the target length.\n"
                    "Do not choose brevity over preservation.\n"
                    "Rewrite only this chunk.\n"
                    "Do not summarize.\n"
                    "Do not compress.\n"
                    "Do not shorten.\n"
                    "Do not skip events.\n"
                    "Do not add a new ending.\n"
                    "Do not repeat previous chunks.\n"
                    "Preserve every meaningful action, observation, dialogue beat, technical detail, and scene beat.\n"
                    "If the source is repetitive, polish the repetition instead of deleting the underlying beat.\n"
                    "Return only the rewritten prose for this chunk.\n\n"
                    "SOURCE_TEXT_START\n"
                    f"{chunk_text}\n"
                    "SOURCE_TEXT_END"
                ),
            },
        ]

        finish_reason = None
        output_parts: list[str] = []
        rewritten_file.write_text("", encoding="utf-8")
        stream = self.create_stream_with_retries(
            client,
            messages,
            self.config.rewrite_temperature,
            self.config.rewrite_top_p,
            self.config.rewrite_max_tokens_per_call,
        )

        with rewritten_file.open("w", encoding="utf-8", newline="\n") as out:
            for api_chunk in stream:
                if self.stop_event.is_set():
                    raise KeyboardInterrupt
                if not api_chunk.choices:
                    continue

                choice = api_chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta.content or ""
                if not delta:
                    continue

                out.write(delta)
                out.flush()
                output_parts.append(delta)
                self.preview(delta)

        output_words = len("".join(output_parts).split())
        ratio = output_words / max(input_words, 1)
        if ratio < self.config.rewrite_min_ratio:
            status = "Compressed"
        elif ratio > self.config.rewrite_max_ratio:
            status = "Expanded"
        else:
            status = "OK"
        self.ui_queue.put((
            "chunk",
            {
                "index": index,
                "input": input_words,
                "output": output_words,
                "ratio": f"{ratio:.0%}",
                "finish": finish_reason or "",
                "status": status,
            },
        ))
        return output_words, ratio, finish_reason

    def run(self, retry_chunk: int | None = None) -> None:
        client = self.client()
        self.status("Rewriting chunks...")
        self.log(f"Model: {self.config.model}")
        self.log(f"Chunk target: {self.config.rewrite_chunk_words:,} words")
        chunks = self.prepare_chunks(clear_outputs=retry_chunk is None)
        total_start = time.time()

        if retry_chunk is not None:
            if retry_chunk < 1 or retry_chunk > len(chunks):
                raise ValueError(f"Chunk {retry_chunk} is outside 1..{len(chunks)}")
            chunk_numbers = [retry_chunk]
            self.log(f"Retrying chunk {retry_chunk}/{len(chunks)}")
        else:
            chunk_numbers = list(range(1, len(chunks) + 1))

        last_finish = None
        for index in chunk_numbers:
            if self.stop_event.is_set():
                raise KeyboardInterrupt
            chunk_text = chunks[index - 1]
            input_words = len(chunk_text.split())
            self.log(f"Chunk {index}/{len(chunks)} - input words: {input_words:,}")
            self.ui_queue.put(("chunk", {"index": index, "status": "Running"}))

            output_words, ratio, last_finish = self.rewrite_chunk(client, chunk_text, index, len(chunks))
            self.rebuild_output(len(chunks))
            self.log(
                f"Chunk {index} done - output words: {output_words:,}, "
                f"ratio: {ratio:.0%}, finish: {last_finish}"
            )
            if ratio < self.config.rewrite_min_ratio:
                self.log(f"WARNING: Chunk {index} may be too compressed.")
            if ratio > self.config.rewrite_max_ratio:
                self.log(f"WARNING: Chunk {index} may be too expanded.")

            if index != chunk_numbers[-1] and self.config.rewrite_pause_seconds > 0:
                for _ in range(self.config.rewrite_pause_seconds):
                    if self.stop_event.is_set():
                        raise KeyboardInterrupt
                    time.sleep(1)

        _, output_file, _, chunks_dir = self.paths()
        final_text = output_file.read_text(encoding="utf-8") if output_file.exists() else ""
        elapsed = time.time() - total_start
        self.log("Done.")
        self.log(f"Saved to: {output_file}")
        self.log(f"Per-chunk files: {chunks_dir}")
        self.log(f"Output words: {len(final_text.split()):,}")
        self.log(f"Output chars: {len(final_text):,}")
        self.log(f"Total time: {elapsed / 60:.1f} minutes")
        self.log(f"Last finish reason: {last_finish}")
        self.status("Done")


class TranslationRunner(LLMRunner):
    def __init__(self, config: GeneratorConfig, ui_queue: queue.Queue[tuple[str, Any]], stop_event: threading.Event):
        super().__init__(config, ui_queue, stop_event)
        self.translation = self.to_translation_config(config)

    def to_translation_config(self, config: GeneratorConfig) -> TranslationConfig:
        return TranslationConfig(
            input_file=resolve_path(config.translation_input_file, "translation_source.txt"),
            output_file=resolve_path(config.translation_output_file, "translation_output.md"),
            segments_dir=resolve_path(config.translation_segments_dir, "translation_segments"),
            source_language=config.translation_source_language,
            target_language=config.translation_target_language,
            register_mode=config.translation_register_mode,
            instruction_file=resolve_optional_path(config.translation_instruction_file),
            glossary_file=resolve_optional_path(config.translation_glossary_file),
            dnt_file=resolve_optional_path(config.translation_dnt_file),
            protected_regex_file=resolve_optional_path(config.translation_protected_regex_file),
            segment_delimiter_style=config.translation_segment_delimiter_style,
            custom_delimiter_regex=config.translation_custom_delimiter_regex,
            chunk_segments=config.translation_chunk_segments,
            max_tokens_per_call=config.translation_max_tokens_per_call,
            temperature=config.translation_temperature,
            top_p=config.translation_top_p,
            pause_seconds=config.translation_pause_seconds,
            validate_after_run=config.translation_validate_after_run,
            validator_profile=config.translation_validator_profile,
            validation_report_file=resolve_path(config.translation_validation_report_file, "translation_validation_report.md"),
            grouped_report=config.translation_grouped_report,
            save_json_report=config.translation_save_json_report,
        )

    def load_profile(self) -> tuple[TranslationProfile, list[str], list[str]]:
        translation = self.translation
        profile = load_translation_profile(None, translation.validator_profile)
        if self.config.translation_instruction_text.strip():
            profile.task_instruction = self.config.translation_instruction_text.strip()
        elif translation.instruction_file and translation.instruction_file.exists():
            if translation.instruction_file.suffix.lower() == ".json":
                profile = load_translation_profile(translation.instruction_file, "")
            else:
                profile.task_instruction = translation.instruction_file.read_text(encoding="utf-8", errors="replace")

        profile.source_language = translation.source_language or profile.source_language
        profile.target_language = translation.target_language or profile.target_language
        profile.default_register_mode = translation.register_mode or profile.default_register_mode
        profile.delimiter_style = translation.segment_delimiter_style or profile.delimiter_style
        profile.delimiter_regex = translation.custom_delimiter_regex or profile.delimiter_regex

        dnt_terms = list(profile.dnt_terms)
        protected_regexes = list(profile.protected_token_regexes)
        if translation.dnt_file:
            dnt_terms.extend(load_line_list(translation.dnt_file))
        if translation.protected_regex_file:
            protected_regexes.extend(load_line_list(translation.protected_regex_file))
        if translation.glossary_file:
            profile.glossary_terms.extend(load_glossary(translation.glossary_file))

        for term in profile.glossary_terms:
            if not term.target_term or term.target_term.strip().upper() == "[TARGET TERM]":
                dnt_terms.append(term.source_term)

        return profile, list(dict.fromkeys(dnt_terms)), list(dict.fromkeys(protected_regexes))

    def parser_for_profile(self, profile: TranslationProfile) -> SegmentParser:
        return SegmentParser(profile.delimiter_style, profile.delimiter_regex)

    def read_segments(self, require_target: bool = True) -> tuple[list[Segment], TranslationProfile, list[str], list[str], SegmentParser]:
        translation = self.translation
        if not translation.input_file.exists():
            raise FileNotFoundError(f"Translation input file not found: {translation.input_file}")
        if require_target and not translation.target_language.strip():
            raise ValueError("Target language is required for translation.")

        profile, dnt_terms, protected_regexes = self.load_profile()
        parser = self.parser_for_profile(profile)
        source_text = translation.input_file.read_text(encoding="utf-8", errors="replace")
        segments = parser.parse(source_text)
        if not segments:
            raise ValueError(
                "No source segments were parsed. Check the segment delimiter style/profile settings."
            )
        return segments, profile, dnt_terms, protected_regexes, parser

    def write_manifest(self, segments: list[Segment], profile: TranslationProfile) -> None:
        translation = self.translation
        manifest = {
            "input_file": str(translation.input_file.resolve()),
            "input_sha256": hashlib.sha256(translation.input_file.read_bytes()).hexdigest(),
            "output_file": str(translation.output_file.resolve()),
            "segment_delimiter_style": translation.segment_delimiter_style,
            "custom_delimiter_regex": translation.custom_delimiter_regex,
            "chunk_segments": translation.chunk_segments,
            "segment_count": len(segments),
            "source_language": translation.source_language,
            "target_language": translation.target_language,
            "register_mode": translation.register_mode,
            "profile": profile.name,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        translation.segments_dir.mkdir(parents=True, exist_ok=True)
        (translation.segments_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    def prepare_segments(self, clear_outputs: bool = True) -> tuple[list[Segment], TranslationProfile, list[str], list[str], SegmentParser]:
        segments, profile, dnt_terms, protected_regexes, parser = self.read_segments(require_target=clear_outputs)
        translation = self.translation
        translation.segments_dir.mkdir(parents=True, exist_ok=True)
        if clear_outputs:
            for pattern in ("source_segment_*.txt", "translated_chunk_*.txt"):
                for old_file in translation.segments_dir.glob(pattern):
                    old_file.unlink(missing_ok=True)
            translation.output_file.unlink(missing_ok=True)
        self.write_manifest(segments, profile)
        for segment in segments:
            (translation.segments_dir / f"source_segment_{segment.id}.txt").write_text(
                parser.render_segment(segment, segment.body),
                encoding="utf-8",
                newline="\n",
            )
            self.ui_queue.put((
                "segment",
                {
                    "id": segment.id,
                    "input": segment.word_count,
                    "output": "",
                    "ratio": "",
                    "finish": "",
                    "status": "Queued",
                    "validation": "",
                    "issues": "",
                },
            ))
        self.log(f"Translation input: {translation.input_file}")
        self.log(f"Segments parsed: {len(segments)}")
        self.log(f"Target language: {translation.target_language}")
        self.log(f"Register mode: {translation.register_mode}")
        self.log(f"Profile: {profile.name}")
        return segments, profile, dnt_terms, protected_regexes, parser

    def build_translation_messages(
        self,
        profile: TranslationProfile,
        parser: SegmentParser,
        segment_chunk: list[Segment],
        chunk_index: int,
        total_chunks: int,
        dnt_terms: list[str],
    ) -> list[dict[str, str]]:
        translation = self.translation
        instruction = profile.instruction_text(
            translation.source_language,
            translation.target_language,
            translation.register_mode,
        )
        glossary_lines = []
        for term in profile.glossary_terms:
            target = term.target_term or "[preserve source term]"
            context = f" | context: {term.context}" if term.context else ""
            note = f" | note: {term.note}" if term.note else ""
            glossary_lines.append(f"- {term.source_term} => {target}{context}{note}")
        dnt_lines = [f"- {term}" for term in dnt_terms]
        source_payload = "\n".join(parser.render_segment(segment, segment.body).rstrip() for segment in segment_chunk)

        user_prompt = (
            f"Translate segment chunk {chunk_index} of {total_chunks}.\n"
            f"Source language: {translation.source_language}\n"
            f"Target language: {translation.target_language}\n"
            f"Register mode: {translation.register_mode}\n\n"
            "Do not translate delimiter lines. Preserve segment IDs and order exactly.\n"
            "Return only the translated segment block(s), with the same delimiters.\n\n"
            "DO_NOT_TRANSLATE_TERMS\n"
            + ("\n".join(dnt_lines) if dnt_lines else "- None")
            + "\n\nGLOSSARY\n"
            + ("\n".join(glossary_lines) if glossary_lines else "- None")
            + "\n\nSOURCE_SEGMENTS_START\n"
            + source_payload
            + "\nSOURCE_SEGMENTS_END"
        )
        return [
            {"role": "system", "content": instruction},
            {"role": "user", "content": user_prompt},
        ]

    def translate_chunk(
        self,
        client: OpenAI,
        profile: TranslationProfile,
        parser: SegmentParser,
        segment_chunk: list[Segment],
        chunk_index: int,
        total_chunks: int,
        dnt_terms: list[str],
    ) -> tuple[str, str | None]:
        translation = self.translation
        chunk_file = translation.segments_dir / f"translated_chunk_{chunk_index:03}.txt"
        messages = self.build_translation_messages(profile, parser, segment_chunk, chunk_index, total_chunks, dnt_terms)
        stream = self.create_stream_with_retries(
            client,
            messages,
            translation.temperature,
            translation.top_p,
            translation.max_tokens_per_call,
        )

        finish_reason = None
        parts: list[str] = []
        chunk_file.write_text("", encoding="utf-8")
        with chunk_file.open("w", encoding="utf-8", newline="\n") as out:
            for api_chunk in stream:
                if self.stop_event.is_set():
                    raise KeyboardInterrupt
                if not api_chunk.choices:
                    continue
                choice = api_chunk.choices[0]
                if choice.finish_reason:
                    finish_reason = choice.finish_reason
                delta = choice.delta.content or ""
                if not delta:
                    continue
                parts.append(delta)
                out.write(delta)
                out.flush()
                self.preview(delta)
                self.ui_queue.put(("translation_preview", delta))

        translated = "".join(parts).strip()
        chunk_file.write_text(translated + "\n", encoding="utf-8", newline="\n")
        for segment in segment_chunk:
            self.ui_queue.put((
                "segment",
                {
                    "id": segment.id,
                    "output": len(translated.split()),
                    "finish": finish_reason or "",
                    "status": "Done",
                },
            ))
        return translated, finish_reason

    def rebuild_output(self, total_chunks: int) -> None:
        translation = self.translation
        parts: list[str] = []
        for index in range(1, total_chunks + 1):
            chunk_file = translation.segments_dir / f"translated_chunk_{index:03}.txt"
            if chunk_file.exists():
                parts.append(chunk_file.read_text(encoding="utf-8", errors="replace").strip())
        translation.output_file.parent.mkdir(parents=True, exist_ok=True)
        translation.output_file.write_text("\n\n".join(part for part in parts if part) + "\n", encoding="utf-8", newline="\n")

    def validate_current_output(self) -> ValidationReport:
        translation = self.translation
        profile, dnt_terms, protected_regexes = self.load_profile()
        parser = self.parser_for_profile(profile)
        validator = TranslationValidator(
            profile,
            parser,
            dnt_terms=dnt_terms,
            protected_regexes=protected_regexes,
            grouped_report=translation.grouped_report,
        )
        report = validator.validate_files(translation.input_file, translation.output_file)
        validator.save_report(
            report,
            translation.validation_report_file,
            grouped=translation.grouped_report,
            save_json=translation.save_json_report,
        )
        self.ui_queue.put(("validation_report", report.format(grouped=translation.grouped_report)))
        self.log(f"Validation report saved to: {translation.validation_report_file}")
        self.log(f"Validation status: {'PASS' if report.passed else 'FAIL'}")
        self.log(f"Errors: {report.error_count}; warnings: {report.warning_count}")
        by_segment: dict[str, dict[str, int]] = {}
        for issue in report.issues:
            sid = issue.segment_id or "GLOBAL"
            bucket = by_segment.setdefault(sid, {"error": 0, "warning": 0})
            if issue.severity in {"critical", "error"}:
                bucket["error"] += 1
            elif issue.severity == "warning":
                bucket["warning"] += 1
        for sid, counts in by_segment.items():
            self.ui_queue.put((
                "segment",
                {
                    "id": sid,
                    "validation": "FAIL" if counts["error"] else "WARN",
                    "issues": f"E{counts['error']} W{counts['warning']}",
                },
            ))
        return report

    def preview_segments(self) -> None:
        segments, profile, _, _, parser = self.prepare_segments(clear_outputs=False)
        sample = "\n".join(parser.render_segment(segment, segment.body).rstrip() for segment in segments[:5])
        self.ui_queue.put(("translation_source", sample))
        self.log(f"Previewed {len(segments)} translation segments using profile: {profile.name}")
        self.status("Translation segments previewed")

    def run(self) -> None:
        client = self.client()
        segments, profile, dnt_terms, _, parser = self.prepare_segments(clear_outputs=True)
        chunks = parser.chunk_segments(segments, self.translation.chunk_segments)
        self.status("Translating segments...")
        self.log(f"Model: {self.config.model}")
        self.log(f"Segment chunks: {len(chunks)}")
        total_start = time.time()
        last_finish = None

        for index, segment_chunk in enumerate(chunks, start=1):
            if self.stop_event.is_set():
                raise KeyboardInterrupt
            ids = ", ".join(segment.id for segment in segment_chunk)
            input_words = sum(segment.word_count for segment in segment_chunk)
            self.log(f"Translation chunk {index}/{len(chunks)} - segments: {ids}; input words: {input_words:,}")
            for segment in segment_chunk:
                self.ui_queue.put(("segment", {"id": segment.id, "status": "Running"}))
            translated, last_finish = self.translate_chunk(client, profile, parser, segment_chunk, index, len(chunks), dnt_terms)
            self.rebuild_output(len(chunks))
            self.log(f"Chunk {index} done - output words: {len(translated.split()):,}, finish: {last_finish}")

            if index != len(chunks) and self.translation.pause_seconds > 0:
                for _ in range(self.translation.pause_seconds):
                    if self.stop_event.is_set():
                        raise KeyboardInterrupt
                    time.sleep(1)

        output_text = self.translation.output_file.read_text(encoding="utf-8", errors="replace")
        self.ui_queue.put(("translation_output", output_text))
        self.log(f"Translation saved to: {self.translation.output_file}")
        self.log(f"Per-segment files: {self.translation.segments_dir}")
        self.log(f"Output words: {len(output_text.split()):,}")
        self.log(f"Total time: {(time.time() - total_start) / 60:.1f} minutes")
        self.log(f"Last finish reason: {last_finish}")

        if self.translation.validate_after_run:
            self.validate_current_output()
        self.status("Done")


class StoryGeneratorApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(f"{APP_NAME} v{APP_VERSION}")
        self.geometry("1360x860")
        self.minsize(1180, 740)

        self.ui_queue: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.stop_event = threading.Event()
        self.worker: threading.Thread | None = None
        self.vars: dict[str, tk.Variable] = {}
        self.field_widgets: dict[str, list[tk.Widget]] = {}
        self.workspace_notebook: ttk.Notebook | None = None
        self.workspace_tab_frames: dict[str, ttk.Frame] = {}
        self.workspace_tab_order: list[str] = []
        self.history_db: HistoryDB | None = None
        self.history_warning: str | None = None
        self.config = load_saved_config()
        self.chunk_rows: dict[str, str] = {}
        self.previous_story_prompt: str | None = None
        self.previous_rewrite_prompt: str | None = None
        self.cost_update_job: str | None = None

        self.configure(bg="#10131a")
        self.create_styles()
        self.create_variables()
        self.create_layout()
        self.populate_from_config(self.config)
        self.attach_traces()
        self.initialize_history()
        self.update_provider_controls()
        self.update_cost_estimates()
        self.after(120, self.process_queue)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    def create_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=("Segoe UI", 10), background="#10131a", foreground="#eef2f7")
        style.configure("TFrame", background="#10131a")
        style.configure("Panel.TFrame", background="#171b24", relief="flat")
        style.configure("TLabel", background="#10131a", foreground="#eef2f7")
        style.configure("Muted.TLabel", background="#10131a", foreground="#9ca9b8")
        style.configure("Panel.TLabel", background="#171b24", foreground="#eef2f7")
        style.configure("Header.TLabel", font=("Segoe UI Semibold", 18), background="#10131a", foreground="#ffffff")
        style.configure("Subheader.TLabel", font=("Segoe UI", 10), background="#10131a", foreground="#9ca9b8")
        style.configure("Cost.TLabel", font=("Segoe UI Semibold", 10), background="#171b24", foreground="#cceee8")
        style.configure("TButton", padding=(10, 7), background="#283246", foreground="#eef2f7", borderwidth=0)
        style.map("TButton", background=[("active", "#34405a"), ("disabled", "#202633")])
        style.configure("Accent.TButton", background="#2f8f83", foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", "#37a091"), ("disabled", "#203a3a")])
        style.configure("Danger.TButton", background="#9a3f48", foreground="#ffffff")
        style.map("Danger.TButton", background=[("active", "#b34b56"), ("disabled", "#33232a")])
        style.configure("TEntry", fieldbackground="#0f1218", foreground="#eef2f7", insertcolor="#ffffff")
        style.configure("TCombobox", fieldbackground="#0f1218", foreground="#eef2f7")
        style.configure("TSpinbox", fieldbackground="#0f1218", foreground="#eef2f7")
        style.configure("TCheckbutton", background="#171b24", foreground="#eef2f7")
        style.configure("TNotebook", background="#10131a", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 8), background="#1b2130", foreground="#c8d2df")
        style.map("TNotebook.Tab", background=[("selected", "#263149")], foreground=[("selected", "#ffffff")])
        style.configure("Treeview", background="#0f1218", fieldbackground="#0f1218", foreground="#eef2f7", rowheight=26)
        style.configure("Treeview.Heading", background="#242d40", foreground="#eef2f7")
        style.configure("Horizontal.TProgressbar", troughcolor="#1b2130", background="#2f8f83")

    def create_variables(self) -> None:
        for field in asdict(GeneratorConfig()).keys():
            if field in BOOL_FIELDS:
                self.vars[field] = tk.BooleanVar()
            elif field in INT_FIELDS:
                self.vars[field] = tk.IntVar()
            elif field in FLOAT_FIELDS:
                self.vars[field] = tk.DoubleVar()
            else:
                self.vars[field] = tk.StringVar()

    def create_layout(self) -> None:
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)
        root.columnconfigure(0, weight=0, minsize=430)
        root.columnconfigure(1, weight=1)
        root.rowconfigure(1, weight=1)

        header = ttk.Frame(root)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        header.columnconfigure(0, weight=1)
        ttk.Label(header, text=f"{APP_NAME} v{APP_VERSION}", style="Header.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header,
            text="Story generation, chunked rewrite, batch translation, validation reports, model presets, and live cost caps.",
            style="Subheader.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(3, 0))

        controls = ttk.Notebook(root)
        controls.grid(row=1, column=0, sticky="nsew", padx=(0, 14))
        self.controls_notebook = controls
        controls.bind("<<NotebookTabChanged>>", lambda _event: self.sync_workspace_for_selected_workflow())

        general = ttk.Frame(controls, style="Panel.TFrame", padding=14)
        routing = ttk.Frame(controls, style="Panel.TFrame", padding=14)
        generation = ttk.Frame(controls, style="Panel.TFrame", padding=14)
        rewrite = ttk.Frame(controls, style="Panel.TFrame", padding=14)
        translation = ttk.Frame(controls, style="Panel.TFrame", padding=14)
        validation = ttk.Frame(controls, style="Panel.TFrame", padding=14)
        prompt_tools_left = ttk.Frame(controls, style="Panel.TFrame", padding=14)
        controls.add(general, text="Model / Provider")
        controls.add(routing, text="Cloud Routing / Cost")
        controls.add(generation, text="Story Generation")
        controls.add(rewrite, text="Rewrite")
        controls.add(translation, text="Translation")
        controls.add(validation, text="QA / Validation")
        controls.add(prompt_tools_left, text="Prompt Tools")
        self.cloud_routing_frame = routing
        for tab in (general, routing, generation, rewrite, translation, validation, prompt_tools_left):
            tab.columnconfigure(1, weight=1)

        self.add_combo(general, 0, "Provider preset", "provider_preset", list(PROVIDER_PRESETS), self.apply_provider_preset)
        self.add_combo(general, 1, "Provider type", "provider_type", list(PROVIDER_TYPES), self.update_provider_controls)
        self.add_entry(general, 2, "Provider name", "provider_name")
        self.add_entry(general, 3, "Base URL", "base_url")
        self.add_entry(general, 4, "Model", "model")
        self.add_combo(general, 5, "Model preset", "model_preset", list(MODEL_PRESETS), self.apply_model_preset)
        self.add_entry(general, 6, "API env var", "api_key_env")
        self.add_entry(general, 7, "API key", "api_key", show="*")
        self.add_check(general, 8, "Requires API key", "requires_api_key")
        self.add_entry(general, 9, "Default API key", "default_api_key_value")
        self.add_check(general, 10, "Streaming", "supports_streaming")
        self.add_check(general, 11, "Structured output", "supports_response_format")
        self.add_check(general, 12, "JSON schema", "supports_json_schema")
        self.add_check(general, 13, "Tools", "supports_tools")
        self.add_check(general, 14, "Reasoning controls", "supports_reasoning_effort")
        self.add_check(general, 15, "List models", "supports_model_listing")
        self.add_numeric(general, 16, "Context tokens", "context_window_tokens", 1, 2000000, 1024, is_int=True, use_slider=False)
        self.add_numeric(general, 17, "Max output tokens", "provider_max_output_tokens", 1, 300000, 1024, is_int=True, use_slider=False)
        ttk.Button(general, text="Test Connection", command=self.start_provider_test).grid(row=18, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        ttk.Button(general, text="List Models", command=self.start_model_list).grid(row=19, column=0, columnspan=3, sticky="ew", pady=4)

        self.add_check(routing, 0, "History enabled", "history_enabled")
        self.add_entry(routing, 1, "History DB", "history_db_file", browse=lambda: self.choose_save_file("history_db_file"))
        ttk.Separator(routing).grid(row=2, column=0, columnspan=3, sticky="ew", pady=10)
        self.add_check(routing, 3, "Safe routing", "safe_routing")
        self.add_entry(routing, 4, "Provider sort", "provider_sort")
        self.add_check(routing, 5, "Allow fallbacks", "allow_fallbacks")
        self.add_numeric(routing, 6, "Prompt $/M cap", "max_prompt_price", 0.0, 1000.0, 0.01, is_int=False, use_slider=False)
        self.add_numeric(routing, 7, "Completion $/M cap", "max_completion_price", 0.0, 1000.0, 0.01, is_int=False, use_slider=False)
        ttk.Separator(routing).grid(row=8, column=0, columnspan=3, sticky="ew", pady=10)
        self.routing_status_var = tk.StringVar()
        self.story_target_var = tk.StringVar()
        self.story_cost_var = tk.StringVar()
        self.rewrite_target_var = tk.StringVar()
        self.rewrite_cost_var = tk.StringVar()
        self.translation_target_var = tk.StringVar()
        self.translation_cost_var = tk.StringVar()
        ttk.Label(routing, textvariable=self.routing_status_var, style="Cost.TLabel").grid(row=9, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        self.enhancer_caps_var = tk.StringVar()
        ttk.Label(routing, textvariable=self.enhancer_caps_var, style="Cost.TLabel").grid(row=10, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        ttk.Label(routing, textvariable=self.story_target_var, style="Cost.TLabel").grid(row=11, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        ttk.Label(routing, textvariable=self.story_cost_var, style="Cost.TLabel").grid(row=12, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        ttk.Label(routing, textvariable=self.rewrite_target_var, style="Cost.TLabel").grid(row=13, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        ttk.Label(routing, textvariable=self.rewrite_cost_var, style="Cost.TLabel").grid(row=14, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        ttk.Label(routing, textvariable=self.translation_target_var, style="Cost.TLabel").grid(row=15, column=0, columnspan=3, sticky="ew", pady=(0, 6))
        ttk.Label(routing, textvariable=self.translation_cost_var, style="Cost.TLabel").grid(row=16, column=0, columnspan=3, sticky="ew")
        ttk.Button(routing, text="Refresh Estimates", command=self.update_cost_estimates).grid(
            row=17, column=0, columnspan=3, sticky="ew", pady=(10, 0)
        )

        self.add_entry(prompt_tools_left, 0, "Enhancer model", "enhancer_model")
        self.add_numeric(prompt_tools_left, 1, "Enhancer temp", "enhancer_temperature", 0.0, 2.0, 0.01, is_int=False)

        self.add_entry(generation, 0, "Output file", "output_file", browse=lambda: self.choose_save_file("output_file"))
        self.add_numeric(generation, 1, "Target min words", "story_target_min_words", 1, 500000, 1000, is_int=True, use_slider=False)
        self.add_numeric(generation, 2, "Target max words", "story_target_max_words", 1, 500000, 1000, is_int=True, use_slider=False)
        self.story_stats_var = tk.StringVar()
        ttk.Label(generation, textvariable=self.story_stats_var, style="Cost.TLabel").grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 8))
        self.add_numeric(generation, 4, "Temperature", "temperature", 0.0, 2.0, 0.01, is_int=False)
        self.add_numeric(generation, 5, "Top P", "top_p", 0.0, 1.0, 0.01, is_int=False)
        self.add_numeric(generation, 6, "Max tokens/call", "max_tokens_per_call", 1, 300000, 1000, is_int=True, use_slider=False)
        self.add_numeric(generation, 7, "Continuations", "max_continuations", 0, 100, 1, is_int=True, use_slider=False)
        self.add_entry(generation, 8, "Continue marker", "continue_marker")
        self.add_numeric(generation, 9, "Retries", "max_retries", 1, 50, 1, is_int=True, use_slider=False)
        self.add_numeric(generation, 10, "Timeout seconds", "timeout_seconds", 30, 30000, 60, is_int=True, use_slider=False)
        self.story_button = ttk.Button(generation, text="Start Story", style="Accent.TButton", command=self.start_story)
        self.story_button.grid(row=11, column=0, columnspan=3, sticky="ew", pady=(12, 4))
        ttk.Button(generation, text="Open Story", command=lambda: self.open_path("output_file", "deepseek_original_novella.md")).grid(
            row=12, column=0, columnspan=3, sticky="ew", pady=4
        )

        self.add_entry(rewrite, 0, "Input file", "rewrite_input_file", browse=lambda: self.choose_open_file("rewrite_input_file"))
        self.add_entry(rewrite, 1, "Output file", "rewrite_output_file", browse=lambda: self.choose_save_file("rewrite_output_file"))
        self.add_entry(rewrite, 2, "Cleaned file", "rewrite_cleaned_file", browse=lambda: self.choose_save_file("rewrite_cleaned_file"))
        self.add_entry(rewrite, 3, "Chunks folder", "rewrite_chunks_dir", browse=lambda: self.choose_folder("rewrite_chunks_dir"))
        ttk.Label(rewrite, textvariable=self.rewrite_target_var, style="Cost.TLabel").grid(row=4, column=0, columnspan=3, sticky="ew", pady=(6, 8))
        self.add_numeric(rewrite, 5, "Chunk words", "rewrite_chunk_words", 200, 5000, 100, is_int=True, use_slider=False)
        self.add_numeric(rewrite, 6, "Temperature", "rewrite_temperature", 0.0, 2.0, 0.01, is_int=False)
        self.add_numeric(rewrite, 7, "Top P", "rewrite_top_p", 0.0, 1.0, 0.01, is_int=False)
        self.add_numeric(rewrite, 8, "Max tokens/chunk", "rewrite_max_tokens_per_call", 1000, 64000, 500, is_int=True, use_slider=False)
        self.add_numeric(rewrite, 9, "Pause seconds", "rewrite_pause_seconds", 0, 120, 1, is_int=True, use_slider=False)
        self.add_numeric(rewrite, 10, "Warn below ratio", "rewrite_min_ratio", 0.1, 1.5, 0.01, is_int=False)
        self.add_numeric(rewrite, 11, "Warn above ratio", "rewrite_max_ratio", 0.1, 3.0, 0.01, is_int=False)
        self.add_numeric(rewrite, 12, "Target min ratio", "rewrite_target_min_ratio", 0.1, 1.5, 0.01, is_int=False)
        self.add_numeric(rewrite, 13, "Target max ratio", "rewrite_target_max_ratio", 0.1, 2.0, 0.01, is_int=False)
        self.add_numeric(rewrite, 14, "Retry chunk", "rewrite_selected_chunk", 1, 500, 1, is_int=True, use_slider=False)
        self.rewrite_button = ttk.Button(rewrite, text="Start Rewrite", style="Accent.TButton", command=self.start_rewrite)
        self.rewrite_button.grid(row=15, column=0, columnspan=3, sticky="ew", pady=(12, 4))
        self.retry_button = ttk.Button(rewrite, text="Retry Chunk", command=self.retry_chunk)
        self.retry_button.grid(row=16, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Button(rewrite, text="Open Rewrite", command=lambda: self.open_path("rewrite_output_file", "novel_rewritten.md")).grid(
            row=17, column=0, columnspan=3, sticky="ew", pady=4
        )

        profile_names = list(builtin_translation_profiles())
        delimiter_styles = list(SegmentParser.STYLES)
        self.add_combo(translation, 0, "Profile", "translation_validator_profile", profile_names, self.load_translation_profile_to_ui)
        self.add_entry(translation, 1, "Input file", "translation_input_file", browse=lambda: self.choose_open_file("translation_input_file"))
        self.add_entry(translation, 2, "Output file", "translation_output_file", browse=lambda: self.choose_save_file("translation_output_file"))
        self.add_entry(translation, 3, "Segments folder", "translation_segments_dir", browse=lambda: self.choose_folder("translation_segments_dir"))
        self.add_entry(translation, 4, "Source language", "translation_source_language")
        self.add_entry(translation, 5, "Target language", "translation_target_language")
        self.add_entry(translation, 6, "Register mode", "translation_register_mode")
        self.add_entry(translation, 7, "Instruction file", "translation_instruction_file", browse=lambda: self.choose_open_file("translation_instruction_file"))
        self.add_entry(translation, 8, "Glossary CSV", "translation_glossary_file", browse=lambda: self.choose_open_file("translation_glossary_file"))
        self.add_entry(translation, 9, "DNT terms", "translation_dnt_file", browse=lambda: self.choose_open_file("translation_dnt_file"))
        self.add_entry(translation, 10, "Protected regexes", "translation_protected_regex_file", browse=lambda: self.choose_open_file("translation_protected_regex_file"))
        self.add_combo(translation, 11, "Delimiter style", "translation_segment_delimiter_style", delimiter_styles, self.update_cost_estimates)
        self.add_entry(translation, 12, "Custom delimiter regex", "translation_custom_delimiter_regex")
        ttk.Label(translation, textvariable=self.translation_target_var, style="Cost.TLabel").grid(row=13, column=0, columnspan=3, sticky="ew", pady=(6, 8))
        self.add_numeric(translation, 14, "Segments/call", "translation_chunk_segments", 1, 1000, 1, is_int=True, use_slider=False)
        self.add_numeric(translation, 15, "Max tokens/call", "translation_max_tokens_per_call", 1, 300000, 1000, is_int=True, use_slider=False)
        self.add_numeric(translation, 16, "Temperature", "translation_temperature", 0.0, 2.0, 0.01, is_int=False)
        self.add_numeric(translation, 17, "Top P", "translation_top_p", 0.0, 1.0, 0.01, is_int=False)
        self.add_numeric(translation, 18, "Pause seconds", "translation_pause_seconds", 0, 30000, 1, is_int=True, use_slider=False)
        self.add_check(translation, 19, "Validate after run", "translation_validate_after_run")
        ttk.Button(translation, text="Load Translation Profile", command=self.load_translation_profile_to_ui).grid(row=20, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        ttk.Button(translation, text="Preview Segments", command=self.preview_translation_segments).grid(row=21, column=0, columnspan=3, sticky="ew", pady=4)
        self.translation_button = ttk.Button(translation, text="Start Translation", style="Accent.TButton", command=self.start_translation)
        self.translation_button.grid(row=22, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Button(translation, text="Open Translation Output", command=lambda: self.open_path("translation_output_file", "translation_output.md")).grid(
            row=23, column=0, columnspan=3, sticky="ew", pady=4
        )

        self.add_entry(validation, 0, "Source file", "translation_input_file", browse=lambda: self.choose_open_file("translation_input_file"))
        self.add_entry(validation, 1, "Translated output", "translation_output_file", browse=lambda: self.choose_open_file("translation_output_file"))
        self.add_combo(validation, 2, "Validation profile", "translation_validator_profile", profile_names, self.load_translation_profile_to_ui)
        self.add_entry(validation, 3, "Report file", "translation_validation_report_file", browse=lambda: self.choose_save_file("translation_validation_report_file"))
        self.add_check(validation, 4, "Grouped report", "translation_grouped_report")
        self.add_check(validation, 5, "Also save JSON", "translation_save_json_report")
        ttk.Label(validation, textvariable=self.translation_cost_var, style="Cost.TLabel").grid(row=6, column=0, columnspan=3, sticky="ew", pady=(8, 8))
        self.validation_button = ttk.Button(validation, text="Validate Translation", style="Accent.TButton", command=self.validate_translation)
        self.validation_button.grid(row=7, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Button(validation, text="Open Validation Report", command=lambda: self.open_path("translation_validation_report_file", "translation_validation_report.md")).grid(row=8, column=0, columnspan=3, sticky="ew", pady=4)
        ttk.Button(validation, text="Open Translation Output", command=lambda: self.open_path("translation_output_file", "translation_output.md")).grid(row=9, column=0, columnspan=3, sticky="ew", pady=4)

        right = ttk.Frame(root)
        right.grid(row=1, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)
        tabs = ttk.Notebook(right)
        tabs.grid(row=0, column=0, sticky="nsew")
        self.workspace_notebook = tabs
        self.workspace_tab_frames.clear()
        self.workspace_tab_order.clear()

        self.prompt_text = self.add_text_tab(tabs, "Story Prompt", "Main prompt", "Consolas", 10)
        system_tab = ttk.Frame(tabs, style="Panel.TFrame", padding=12)
        system_tab.rowconfigure((1, 3), weight=1)
        system_tab.columnconfigure(0, weight=1)
        tabs.add(system_tab, text="System")
        self.register_workspace_tab("System", system_tab)
        ttk.Label(system_tab, text="First call system prompt", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        self.system_text = self.make_text(system_tab, "Consolas", 10, height=7)
        self.system_text.grid(row=1, column=0, sticky="nsew", pady=(8, 12))
        ttk.Label(system_tab, text="Continuation system prompt", style="Panel.TLabel").grid(row=2, column=0, sticky="w")
        self.continuation_system_text = self.make_text(system_tab, "Consolas", 10, height=7)
        self.continuation_system_text.grid(row=3, column=0, sticky="nsew", pady=(8, 0))
        self.rewrite_prompt_text = self.add_text_tab(tabs, "Rewrite Prompt", "Chunk rewrite system prompt", "Consolas", 10)
        self.translation_instruction_text = self.add_text_tab(tabs, "Translation Instructions", "Translation instruction/profile text", "Consolas", 10)
        self.translation_source_text = self.add_text_tab(tabs, "Translation Source Preview", "Parsed source segment preview", "Consolas", 10)
        self.translation_output_text = self.add_text_tab(tabs, "Translation Output Preview", "Translated output preview", "Georgia", 11)
        self.validation_report_text = self.add_text_tab(tabs, "Validation Report", "Grouped validation report", "Consolas", 10)
        self.create_prompt_tools_tab(tabs)
        self.preview_text = self.add_text_tab(tabs, "Live Output", "Streaming preview", "Georgia", 11)

        chunk_tab = ttk.Frame(tabs, style="Panel.TFrame", padding=12)
        chunk_tab.rowconfigure(1, weight=1)
        chunk_tab.columnconfigure(0, weight=1)
        tabs.add(chunk_tab, text="Chunks/Segments")
        self.register_workspace_tab("Chunks/Segments", chunk_tab)
        ttk.Label(chunk_tab, text="Chunk and segment status", style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        self.chunk_tree = ttk.Treeview(
            chunk_tab,
            columns=("id", "input", "output", "ratio", "finish", "status", "validation", "issues"),
            show="headings",
        )
        for column, width in (
            ("id", 80),
            ("input", 90),
            ("output", 90),
            ("ratio", 80),
            ("finish", 110),
            ("status", 130),
            ("validation", 110),
            ("issues", 110),
        ):
            self.chunk_tree.heading(column, text=column.title())
            self.chunk_tree.column(column, width=width, anchor="center")
        self.chunk_tree.grid(row=1, column=0, sticky="nsew")
        self.chunk_tree.bind("<<TreeviewSelect>>", self.select_chunk_from_table)

        self.log_text = self.add_text_tab(tabs, "Log", "Run log", "Consolas", 10)
        self.sync_workspace_for_selected_workflow()

        footer = ttk.Frame(root)
        footer.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(12, 0))
        footer.columnconfigure(0, weight=1)
        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(footer, textvariable=self.status_var, style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Button(footer, text="Save Settings", command=self.save_settings).grid(row=0, column=1, padx=4)
        self.stop_button = ttk.Button(footer, text="Stop", style="Danger.TButton", command=self.stop_generation, state="disabled")
        self.stop_button.grid(row=0, column=2, padx=4)
        self.progress = ttk.Progressbar(footer, mode="indeterminate", length=140)
        self.progress.grid(row=0, column=3, padx=(10, 0))

    def sync_workspace_for_selected_workflow(self) -> None:
        if self.workspace_notebook is None or not hasattr(self, "controls_notebook"):
            return
        selected = self.controls_notebook.select()
        if not selected:
            return

        selected_title = self.controls_notebook.tab(selected, "text")
        tab_map = {
            "Model / Provider": ["Log"],
            "Cloud Routing / Cost": ["Log"],
            "Story Generation": ["Story Prompt", "System", "Live Output", "Log"],
            "Rewrite": ["Rewrite Prompt", "Live Output", "Chunks/Segments", "Log"],
            "Translation": [
                "Translation Instructions",
                "Translation Source Preview",
                "Translation Output Preview",
                "Chunks/Segments",
                "Log",
            ],
            "QA / Validation": [
                "Validation Report",
                "Translation Source Preview",
                "Translation Output Preview",
                "Chunks/Segments",
                "Log",
            ],
            "Prompt Tools": ["Prompt Tools", "Log"],
        }

        for tab_id in list(self.workspace_notebook.tabs()):
            self.workspace_notebook.forget(tab_id)

        for title in tab_map.get(selected_title, ["Log"]):
            frame = self.workspace_tab_frames.get(title)
            if frame is not None:
                self.workspace_notebook.add(frame, text=title)

    def make_text(self, parent: ttk.Frame, family: str, size: int, height: int | None = None) -> scrolledtext.ScrolledText:
        return scrolledtext.ScrolledText(
            parent,
            wrap="word",
            undo=True,
            height=height,
            bg="#0f1218",
            fg="#eef2f7",
            insertbackground="#ffffff",
            selectbackground="#2f8f83",
            relief="flat",
            font=(family, size),
        )

    def register_workspace_tab(self, title: str, frame: ttk.Frame) -> None:
        self.workspace_tab_frames[title] = frame
        if title not in self.workspace_tab_order:
            self.workspace_tab_order.append(title)

    def add_text_tab(self, tabs: ttk.Notebook, title: str, label: str, family: str, size: int) -> scrolledtext.ScrolledText:
        tab = ttk.Frame(tabs, style="Panel.TFrame", padding=12)
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)
        tabs.add(tab, text=title)
        self.register_workspace_tab(title, tab)
        ttk.Label(tab, text=label, style="Panel.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 8))
        text = self.make_text(tab, family, size)
        text.grid(row=1, column=0, sticky="nsew")
        return text

    def create_prompt_tools_tab(self, tabs: ttk.Notebook) -> None:
        tab = ttk.Frame(tabs, style="Panel.TFrame", padding=12)
        tab.columnconfigure(0, weight=1)
        tab.rowconfigure(2, weight=1)
        tab.rowconfigure(5, weight=1)
        tabs.add(tab, text="Prompt Tools")
        self.register_workspace_tab("Prompt Tools", tab)

        self.enhancer_source_var = tk.StringVar(value="Story Prompt")
        self.enhancer_mode_var = tk.StringVar(value="Enhance Story Prompt")
        self.enhancer_counts_var = tk.StringVar(value="Source words: 0 | Enhanced words: 0")

        tools = ttk.Frame(tab, style="Panel.TFrame")
        tools.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        tools.columnconfigure(1, weight=1)
        tools.columnconfigure(3, weight=1)
        ttk.Label(tools, text="Source", style="Panel.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        ttk.Combobox(
            tools,
            textvariable=self.enhancer_source_var,
            values=["Story Prompt", "Rewrite Prompt"],
            state="readonly",
            width=18,
        ).grid(row=0, column=1, sticky="ew", padx=(0, 10))
        ttk.Label(tools, text="Mode", style="Panel.TLabel").grid(row=0, column=2, sticky="w", padx=(0, 8))
        ttk.Combobox(
            tools,
            textvariable=self.enhancer_mode_var,
            values=list(PromptEnhancer.MODE_INSTRUCTIONS),
            state="readonly",
            width=24,
        ).grid(row=0, column=3, sticky="ew")

        buttons = ttk.Frame(tab, style="Panel.TFrame")
        buttons.grid(row=1, column=0, sticky="ew", pady=(0, 8))
        for column in range(6):
            buttons.columnconfigure(column, weight=1)
        ttk.Button(buttons, text="Load Source", command=self.load_prompt_source).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        self.enhance_button = ttk.Button(buttons, text="Enhance Prompt", style="Accent.TButton", command=self.start_prompt_enhancer)
        self.enhance_button.grid(row=0, column=1, sticky="ew", padx=5)
        ttk.Button(buttons, text="Apply to Story", command=self.apply_enhanced_to_story).grid(row=0, column=2, sticky="ew", padx=5)
        ttk.Button(buttons, text="Apply to Rewrite", command=self.apply_enhanced_to_rewrite).grid(row=0, column=3, sticky="ew", padx=5)
        ttk.Button(buttons, text="Restore Previous", command=self.restore_previous_prompt).grid(row=0, column=4, sticky="ew", padx=5)
        ttk.Button(buttons, text="Clear", command=self.clear_prompt_tools).grid(row=0, column=5, sticky="ew", padx=(5, 0))

        ttk.Label(tab, text="Source prompt", style="Panel.TLabel").grid(row=2, column=0, sticky="nw")
        self.enhancer_source_text = self.make_text(tab, "Consolas", 10, height=8)
        self.enhancer_source_text.grid(row=2, column=0, sticky="nsew", pady=(24, 12))

        ttk.Label(tab, text="Enhanced prompt", style="Panel.TLabel").grid(row=4, column=0, sticky="w", pady=(0, 8))
        self.enhancer_output_text = self.make_text(tab, "Consolas", 10, height=8)
        self.enhancer_output_text.grid(row=5, column=0, sticky="nsew")
        ttk.Label(tab, textvariable=self.enhancer_counts_var, style="Panel.TLabel").grid(row=6, column=0, sticky="w", pady=(8, 0))

        self.enhancer_source_text.bind("<KeyRelease>", lambda _event: self.update_prompt_tool_counts())
        self.enhancer_output_text.bind("<KeyRelease>", lambda _event: self.update_prompt_tool_counts())

    def add_entry(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        field: str,
        browse: Callable[[], None] | None = None,
        show: str | None = None,
    ) -> None:
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=5)
        entry = ttk.Entry(parent, textvariable=self.vars[field], show=show)
        entry.grid(row=row, column=1, sticky="ew", pady=5, padx=(10, 0))
        self.field_widgets.setdefault(field, []).append(entry)
        if browse:
            button = ttk.Button(parent, text="...", width=3, command=browse)
            button.grid(row=row, column=2, sticky="e", padx=(6, 0))
            self.field_widgets.setdefault(field, []).append(button)

    def add_combo(self, parent: ttk.Frame, row: int, label: str, field: str, values: list[str], callback: Callable[[], None]) -> None:
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=5)
        combo = ttk.Combobox(parent, textvariable=self.vars[field], values=values, state="readonly")
        combo.grid(row=row, column=1, columnspan=2, sticky="ew", pady=5, padx=(10, 0))
        combo.bind("<<ComboboxSelected>>", lambda _event: callback())
        self.field_widgets.setdefault(field, []).append(combo)

    def add_check(self, parent: ttk.Frame, row: int, label: str, field: str) -> None:
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=5)
        check = ttk.Checkbutton(parent, variable=self.vars[field])
        check.grid(row=row, column=1, sticky="w", pady=5, padx=(10, 0))
        self.field_widgets.setdefault(field, []).append(check)

    def add_numeric(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        field: str,
        minimum: float,
        maximum: float,
        step: float,
        is_int: bool,
        use_slider: bool = True,
    ) -> None:
        ttk.Label(parent, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", pady=7)
        frame = ttk.Frame(parent, style="Panel.TFrame")
        frame.grid(row=row, column=1, columnspan=2, sticky="ew", pady=7, padx=(10, 0))
        frame.columnconfigure(0, weight=1)
        if use_slider:
            scale = tk.Scale(
                frame,
                variable=self.vars[field],
                from_=minimum,
                to=maximum,
                orient="horizontal",
                resolution=step,
                showvalue=False,
                bg="#171b24",
                fg="#eef2f7",
                highlightthickness=0,
                troughcolor="#263149",
                activebackground="#2f8f83",
            )
            scale.grid(row=0, column=0, sticky="ew", padx=(0, 8))
            self.field_widgets.setdefault(field, []).append(scale)
            spin_column = 1
        else:
            spin_column = 0

        spin_width = 12 if not use_slider else 9
        spin = ttk.Spinbox(frame, textvariable=self.vars[field], from_=minimum, to=maximum, increment=step, width=spin_width)
        spin.grid(row=0, column=spin_column, sticky="e")
        self.field_widgets.setdefault(field, []).append(spin)

    def populate_from_config(self, config: GeneratorConfig) -> None:
        for field, var in self.vars.items():
            value = getattr(config, field)
            var.set(value)
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", config.story_prompt)
        self.system_text.delete("1.0", "end")
        self.system_text.insert("1.0", config.system_prompt)
        self.continuation_system_text.delete("1.0", "end")
        self.continuation_system_text.insert("1.0", config.continuation_system_prompt)
        self.rewrite_prompt_text.delete("1.0", "end")
        self.rewrite_prompt_text.insert("1.0", config.rewrite_system_prompt)
        self.translation_instruction_text.delete("1.0", "end")
        if config.translation_instruction_text.strip():
            instruction_text = config.translation_instruction_text
        else:
            instruction_path = resolve_optional_path(config.translation_instruction_file)
            if instruction_path and instruction_path.exists():
                instruction_text = instruction_path.read_text(encoding="utf-8", errors="replace")
            else:
                instruction_text = load_translation_profile(None, config.translation_validator_profile).task_instruction
        self.translation_instruction_text.insert("1.0", instruction_text)

    def attach_traces(self) -> None:
        for field in (
            "max_prompt_price",
            "max_completion_price",
            "safe_routing",
            "allow_fallbacks",
            "provider_type",
            "base_url",
            "model",
            "enhancer_model",
            "context_window_tokens",
            "provider_max_output_tokens",
            "story_target_min_words",
            "story_target_max_words",
            "max_tokens_per_call",
            "max_continuations",
            "rewrite_input_file",
            "rewrite_chunk_words",
            "rewrite_max_tokens_per_call",
            "rewrite_min_ratio",
            "rewrite_max_ratio",
            "rewrite_target_min_ratio",
            "rewrite_target_max_ratio",
            "translation_input_file",
            "translation_chunk_segments",
            "translation_max_tokens_per_call",
            "translation_source_language",
            "translation_target_language",
            "translation_segment_delimiter_style",
            "translation_custom_delimiter_regex",
        ):
            self.vars[field].trace_add("write", lambda *_args: self.schedule_cost_update())
        for field in ("provider_type", "supports_response_format", "supports_json_schema"):
            self.vars[field].trace_add("write", lambda *_args: self.update_provider_controls())

    def schedule_cost_update(self) -> None:
        if self.cost_update_job is not None:
            self.after_cancel(self.cost_update_job)
        self.cost_update_job = self.after(450, self.run_scheduled_cost_update)

    def run_scheduled_cost_update(self) -> None:
        self.cost_update_job = None
        self.update_cost_estimates()

    def collect_config(self) -> GeneratorConfig:
        def get_float_bounded(field: str, minimum: float, maximum: float) -> float:
            try:
                value = float(str(self.vars[field].get()).strip())
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field} must be a number.") from exc

            if not minimum <= value <= maximum:
                raise ValueError(f"{field} must be between {minimum} and {maximum}. Got {value}.")
            return value

        def get_int_bounded(field: str, minimum: int, maximum: int) -> int:
            try:
                raw_value = float(str(self.vars[field].get()).strip())
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field} must be an integer.") from exc

            if not raw_value.is_integer():
                raise ValueError(f"{field} must be an integer. Got {raw_value}.")

            value = int(raw_value)
            if not minimum <= value <= maximum:
                raise ValueError(f"{field} must be between {minimum} and {maximum}. Got {value}.")
            return value

        values: dict[str, Any] = {}
        for field, var in self.vars.items():
            raw = var.get()
            if field in BOOL_FIELDS:
                values[field] = bool(raw)
            elif field in INT_FIELDS or field in FLOAT_FIELDS:
                continue
            else:
                values[field] = str(raw).strip()

        values["max_prompt_price"] = get_float_bounded("max_prompt_price", 0.0, 1000.0)
        values["max_completion_price"] = get_float_bounded("max_completion_price", 0.0, 1000.0)
        values["enhancer_temperature"] = get_float_bounded("enhancer_temperature", 0.0, 2.0)
        values["temperature"] = get_float_bounded("temperature", 0.0, 2.0)
        values["top_p"] = get_float_bounded("top_p", 0.0, 1.0)
        values["story_target_min_words"] = get_int_bounded("story_target_min_words", 1, 500000)
        values["story_target_max_words"] = get_int_bounded("story_target_max_words", 1, 500000)
        values["max_tokens_per_call"] = get_int_bounded("max_tokens_per_call", 1, 300000)
        values["max_continuations"] = get_int_bounded("max_continuations", 0, 100)
        values["max_retries"] = get_int_bounded("max_retries", 1, 50)
        values["timeout_seconds"] = get_int_bounded("timeout_seconds", 30, 30000)
        values["context_window_tokens"] = get_int_bounded("context_window_tokens", 1, 2_000_000)
        values["provider_max_output_tokens"] = get_int_bounded("provider_max_output_tokens", 1, 300000)

        values["rewrite_temperature"] = get_float_bounded("rewrite_temperature", 0.0, 2.0)
        values["rewrite_top_p"] = get_float_bounded("rewrite_top_p", 0.0, 1.0)
        values["rewrite_chunk_words"] = get_int_bounded("rewrite_chunk_words", 1, 300000)
        values["rewrite_max_tokens_per_call"] = get_int_bounded("rewrite_max_tokens_per_call", 1, 300000)
        values["rewrite_pause_seconds"] = get_int_bounded("rewrite_pause_seconds", 0, 30000)
        values["rewrite_selected_chunk"] = get_int_bounded("rewrite_selected_chunk", 1, 100000)
        values["rewrite_min_ratio"] = get_float_bounded("rewrite_min_ratio", 0.0, 10.0)
        values["rewrite_max_ratio"] = get_float_bounded("rewrite_max_ratio", 0.0, 10.0)
        values["rewrite_target_min_ratio"] = get_float_bounded("rewrite_target_min_ratio", 0.0, 10.0)
        values["rewrite_target_max_ratio"] = get_float_bounded("rewrite_target_max_ratio", 0.0, 10.0)
        values["translation_temperature"] = get_float_bounded("translation_temperature", 0.0, 2.0)
        values["translation_top_p"] = get_float_bounded("translation_top_p", 0.0, 1.0)
        values["translation_chunk_segments"] = get_int_bounded("translation_chunk_segments", 1, 100000)
        values["translation_max_tokens_per_call"] = get_int_bounded("translation_max_tokens_per_call", 1, 300000)
        values["translation_pause_seconds"] = get_int_bounded("translation_pause_seconds", 0, 30000)

        if values["story_target_min_words"] > values["story_target_max_words"]:
            raise ValueError("story_target_min_words cannot be greater than story_target_max_words.")
        if values["rewrite_target_min_ratio"] > values["rewrite_target_max_ratio"]:
            raise ValueError("rewrite_target_min_ratio cannot be greater than rewrite_target_max_ratio.")
        if values["rewrite_min_ratio"] > values["rewrite_max_ratio"]:
            raise ValueError("rewrite_min_ratio cannot be greater than rewrite_max_ratio.")
        if values["provider_type"] not in PROVIDER_TYPES:
            raise ValueError(f"provider_type must be one of: {', '.join(PROVIDER_TYPES)}.")

        values["system_prompt"] = self.system_text.get("1.0", "end-1c").strip()
        values["continuation_system_prompt"] = self.continuation_system_text.get("1.0", "end-1c").strip()
        values["story_prompt"] = self.prompt_text.get("1.0", "end-1c").strip()
        values["rewrite_system_prompt"] = self.rewrite_prompt_text.get("1.0", "end-1c").strip()
        values["translation_instruction_text"] = self.translation_instruction_text.get("1.0", "end-1c").strip()
        return GeneratorConfig(**values)

    def apply_model_preset(self) -> None:
        preset = MODEL_PRESETS.get(str(self.vars["model_preset"].get()), {})
        if not preset:
            return
        self.vars["model"].set(preset["model"])
        self.vars["enhancer_model"].set(preset["model"])
        self.vars["max_prompt_price"].set(preset["prompt"])
        self.vars["max_completion_price"].set(preset["completion"])
        self.vars["temperature"].set(preset["temperature"])
        self.vars["top_p"].set(preset["top_p"])
        self.update_cost_estimates()

    def apply_provider_preset(self) -> None:
        values: dict[str, Any] = {}
        apply_provider_preset_values(values, str(self.vars["provider_preset"].get()))
        for field, value in values.items():
            if field in self.vars:
                self.vars[field].set(value)
        self.update_provider_controls()
        self.update_cost_estimates()

    def set_field_enabled(self, fields: tuple[str, ...], enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for field in fields:
            for widget in self.field_widgets.get(field, []):
                try:
                    if isinstance(widget, ttk.Combobox) and enabled:
                        widget.configure(state="readonly")
                    else:
                        widget.configure(state=state)
                except tk.TclError:
                    pass

    def update_provider_controls(self) -> None:
        provider_type = str(self.vars.get("provider_type", tk.StringVar(value="openrouter")).get())
        is_openrouter = provider_type == "openrouter"
        self.set_field_enabled(
            ("safe_routing", "provider_sort", "allow_fallbacks", "max_prompt_price", "max_completion_price"),
            is_openrouter,
        )
        if not bool(self.vars["supports_response_format"].get()):
            self.set_field_enabled(("supports_json_schema",), False)
        else:
            self.set_field_enabled(("supports_json_schema",), True)
        self.schedule_cost_update()

    def initialize_history(self) -> None:
        self.close_history()
        try:
            config = self.collect_config()
        except Exception as exc:
            self.history_warning = f"History disabled because settings are invalid: {exc}"
            return
        if not config.history_enabled:
            self.history_warning = "History disabled in settings."
            return
        try:
            self.history_db = HistoryDB(resolve_history_db_path(config.history_db_file))
            if self.history_db.warning:
                self.history_warning = self.history_db.warning
                self.history_db = None
            else:
                self.history_warning = None
                self.ui_queue.put(("log", f"History DB ready: {resolve_history_db_path(config.history_db_file)}"))
        except Exception as exc:
            self.history_db = None
            self.history_warning = f"History DB unavailable; continuing without DB. {exc}"
            self.ui_queue.put(("log", self.history_warning))

    def close_history(self) -> None:
        if self.history_db is not None:
            try:
                self.history_db.close()
            except Exception:
                pass
        self.history_db = None

    def history_run_paths(self, workflow_type: str, config: GeneratorConfig) -> dict[str, str | None]:
        if workflow_type == "story":
            return {"output_file": str(resolve_path(config.output_file, "deepseek_original_novella.md"))}
        if workflow_type == "rewrite":
            return {
                "input_file": str(resolve_path(config.rewrite_input_file, "novel.md")),
                "output_file": str(resolve_path(config.rewrite_output_file, "novel_rewritten.md")),
                "working_dir": str(resolve_path(config.rewrite_chunks_dir, "rewrite_chunks")),
                "manifest_file": str(resolve_path(config.rewrite_chunks_dir, "rewrite_chunks") / "manifest.json"),
            }
        if workflow_type == "translation":
            return {
                "input_file": str(resolve_path(config.translation_input_file, "translation_source.txt")),
                "output_file": str(resolve_path(config.translation_output_file, "translation_output.md")),
                "segments_dir": str(resolve_path(config.translation_segments_dir, "translation_segments")),
                "manifest_file": str(resolve_path(config.translation_segments_dir, "translation_segments") / "manifest.json"),
                "report_file": str(resolve_path(config.translation_validation_report_file, "translation_validation_report.md")),
            }
        if workflow_type == "validation":
            return {
                "input_file": str(resolve_path(config.translation_input_file, "translation_source.txt")),
                "output_file": str(resolve_path(config.translation_output_file, "translation_output.md")),
                "report_file": str(resolve_path(config.translation_validation_report_file, "translation_validation_report.md")),
            }
        return {}

    def update_cost_estimates(self) -> None:
        try:
            config = self.collect_config()
        except Exception:
            return

        provider = provider_from_config(config)
        if provider.is_openrouter:
            fallback_text = "fallbacks blocked" if not config.allow_fallbacks else "fallbacks allowed"
            safe_text = "Safe routing ON" if config.safe_routing else "Safe routing OFF"
            self.routing_status_var.set(
                f"{safe_text} - {fallback_text} - max output ${config.max_completion_price:.4f}/M"
            )
        elif provider.is_local:
            self.routing_status_var.set(
                f"Local mode: requests go to {provider.base_url}; API cost not estimated"
            )
        else:
            self.routing_status_var.set(
                f"{provider.provider_name}: cloud/custom endpoint; OpenRouter price caps disabled"
            )
        if provider.is_openrouter:
            if config.enhancer_model == config.model:
                self.enhancer_caps_var.set("Enhancer uses main model and OpenRouter routing caps")
            else:
                self.enhancer_caps_var.set("Enhancer uses OpenRouter routing caps; raise caps if that model is pricier")
        else:
            self.enhancer_caps_var.set("Enhancer uses the active provider endpoint; API cost not estimated here")

        story_min_tokens = estimate_tokens_from_words(config.story_target_min_words)
        story_max_tokens = estimate_tokens_from_words(config.story_target_max_words)
        story_cap_tokens = config.max_tokens_per_call * (config.max_continuations + 1)
        story_cap_note = " cap may be low" if story_cap_tokens < story_min_tokens else ""
        self.story_target_var.set(
            f"Story target: {config.story_target_min_words:,}-{config.story_target_max_words:,} words"
        )
        self.story_stats_var.set(
            f"Estimated output tokens: {story_min_tokens:,}-{story_max_tokens:,}; hard cap: {story_cap_tokens:,}.{story_cap_note}"
        )

        story_prompt_tokens = estimate_tokens(config.system_prompt + "\n" + config.story_prompt)
        story_completion_tokens = min(story_max_tokens, story_cap_tokens)
        story_total_tokens = story_prompt_tokens + story_completion_tokens
        context_note = " context warning" if story_total_tokens > config.context_window_tokens else ""
        if provider.is_openrouter:
            story_base, story_recharge = money(story_prompt_tokens, story_completion_tokens, config)
            self.story_cost_var.set(
                f"Story target estimate: ${story_base:.4f} base / ${story_recharge:.4f} with 1.28x.{context_note}"
            )
        else:
            self.story_cost_var.set(
                f"Story token plan: ~{story_total_tokens:,}/{config.context_window_tokens:,} context tokens. Local/custom API cost not estimated.{context_note}"
            )

        input_file = resolve_path(config.rewrite_input_file, "novel.md")
        if not input_file.exists():
            self.rewrite_target_var.set("Rewrite target: select input file first")
            self.rewrite_cost_var.set("Rewrite estimate: select input file first")
        else:
            try:
                cleaned = preclean_text(input_file.read_text(encoding="utf-8"))
                input_words = len(cleaned.split())
                chunks = max(1, math.ceil(input_words / max(config.rewrite_chunk_words, 1)))
                expected_min = int(input_words * config.rewrite_target_min_ratio)
                expected_max = int(input_words * config.rewrite_target_max_ratio)
                rewrite_prompt_tokens = estimate_tokens(cleaned) + chunks * estimate_tokens(config.rewrite_system_prompt)
            except Exception:
                self.rewrite_target_var.set("Rewrite target: could not read input file")
                self.rewrite_cost_var.set("Rewrite estimate: unavailable")
            else:
                self.rewrite_target_var.set(
                    f"Rewrite target: {input_words:,} input words, {chunks} chunks, output {expected_min:,}-{expected_max:,} words"
                )
                rewrite_completion_tokens = min(
                    chunks * config.rewrite_max_tokens_per_call,
                    estimate_tokens_from_words(expected_max),
                )
                rewrite_total_tokens = rewrite_prompt_tokens + rewrite_completion_tokens
                rewrite_context_note = " context warning" if rewrite_total_tokens > config.context_window_tokens else ""
                if provider.is_openrouter:
                    rewrite_base, rewrite_recharge = money(rewrite_prompt_tokens, rewrite_completion_tokens, config)
                    self.rewrite_cost_var.set(
                        f"Rewrite target estimate: ${rewrite_base:.4f} base / ${rewrite_recharge:.4f} with 1.28x.{rewrite_context_note}"
                    )
                else:
                    self.rewrite_cost_var.set(
                        f"Rewrite token plan: ~{rewrite_total_tokens:,}/{config.context_window_tokens:,} context tokens. Local/custom API cost not estimated.{rewrite_context_note}"
                    )

        translation_input = resolve_path(config.translation_input_file, "translation_source.txt")
        if not translation_input.exists():
            self.translation_target_var.set("Translation target: select input file first")
            self.translation_cost_var.set("Translation estimate: select input file first")
            return

        try:
            profile = load_translation_profile(None, config.translation_validator_profile)
            if config.translation_instruction_text.strip():
                profile.task_instruction = config.translation_instruction_text.strip()
            profile.delimiter_style = config.translation_segment_delimiter_style
            profile.delimiter_regex = config.translation_custom_delimiter_regex
            parser = SegmentParser(profile.delimiter_style, profile.delimiter_regex)
            source_text = translation_input.read_text(encoding="utf-8", errors="replace")
            segments = parser.parse(source_text)
            input_words = len(source_text.split())
            segment_chunks = max(1, math.ceil(max(len(segments), 1) / max(config.translation_chunk_segments, 1)))
            prompt_tokens = estimate_tokens(source_text) + segment_chunks * estimate_tokens(
                config.translation_instruction_text or profile.task_instruction
            )
        except Exception:
            self.translation_target_var.set("Translation target: could not parse input")
            self.translation_cost_var.set("Translation estimate: unavailable")
            return

        output_tokens = min(
            segment_chunks * config.translation_max_tokens_per_call,
            max(estimate_tokens_from_words(input_words), 1),
        )
        total_tokens = prompt_tokens + output_tokens
        translation_context_note = " context warning" if total_tokens > config.context_window_tokens else ""
        target_language = config.translation_target_language or "target language not set"
        self.translation_target_var.set(
            f"Translation target: {len(segments):,} segments, {segment_chunks:,} calls, {input_words:,} input words -> {target_language}"
        )
        if provider.is_openrouter:
            base, recharge = money(prompt_tokens, output_tokens, config)
            self.translation_cost_var.set(
                f"Translation estimate: ${base:.4f} base / ${recharge:.4f} with 1.28x.{translation_context_note}"
            )
        else:
            self.translation_cost_var.set(
                f"Translation token plan: ~{total_tokens:,}/{config.context_window_tokens:,} context tokens. Local/custom API cost not estimated.{translation_context_note}"
            )

    def save_settings(self) -> None:
        try:
            config = self.collect_config()
            data = asdict(config)
            data["api_key"] = ""
            CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
            self.status_var.set(f"Settings saved to {CONFIG_FILE.name}")
            self.initialize_history()
        except Exception as exc:
            messagebox.showerror("Could not save settings", str(exc))

    def choose_open_file(self, field: str) -> None:
        filename = filedialog.askopenfilename(
            title="Choose input file",
            initialdir=APP_DIR,
            filetypes=[("Common text/data", "*.md *.txt *.csv *.json"), ("All files", "*.*")],
        )
        if filename:
            self.vars[field].set(filename)
            if field == "translation_instruction_file":
                self.load_translation_profile_to_ui()
            self.update_cost_estimates()

    def choose_save_file(self, field: str) -> None:
        if field == "history_db_file":
            defaultextension = ".sqlite3"
            filetypes = [("SQLite database", "*.sqlite3 *.db"), ("All files", "*.*")]
        else:
            defaultextension = ".md"
            filetypes = [("Markdown", "*.md"), ("Text", "*.txt"), ("All files", "*.*")]
        filename = filedialog.asksaveasfilename(
            title="Choose output file",
            initialdir=APP_DIR,
            defaultextension=defaultextension,
            filetypes=filetypes,
        )
        if filename:
            self.vars[field].set(filename)

    def choose_folder(self, field: str) -> None:
        folder = filedialog.askdirectory(title="Choose folder", initialdir=APP_DIR)
        if folder:
            self.vars[field].set(folder)
            self.update_cost_estimates()

    def open_path(self, field: str, default_name: str) -> None:
        try:
            path = resolve_path(str(self.vars[field].get()), default_name)
            if not path.exists():
                messagebox.showinfo("Output not found", f"No file exists yet:\n{path}")
                return
            os.startfile(path)  # type: ignore[attr-defined]
        except Exception as exc:
            messagebox.showerror("Could not open file", str(exc))

    def load_translation_profile_to_ui(self) -> None:
        try:
            profile_name = str(self.vars["translation_validator_profile"].get())
            instruction_path = resolve_optional_path(str(self.vars["translation_instruction_file"].get()))
            profile = load_translation_profile(None, profile_name)
            if instruction_path and instruction_path.exists() and instruction_path.suffix.lower() == ".json":
                profile = load_translation_profile(instruction_path, "")
                instruction_text = profile.task_instruction
            elif instruction_path and instruction_path.exists():
                instruction_text = instruction_path.read_text(encoding="utf-8", errors="replace")
            else:
                instruction_text = profile.task_instruction

            self.translation_instruction_text.delete("1.0", "end")
            self.translation_instruction_text.insert("1.0", instruction_text)

            if not str(self.vars["translation_source_language"].get()).strip():
                self.vars["translation_source_language"].set(profile.source_language)
            if not str(self.vars["translation_register_mode"].get()).strip():
                self.vars["translation_register_mode"].set(profile.default_register_mode)
            if profile.delimiter_style:
                self.vars["translation_segment_delimiter_style"].set(profile.delimiter_style)
            if profile.delimiter_regex:
                self.vars["translation_custom_delimiter_regex"].set(profile.delimiter_regex)

            self.status_var.set(f"Loaded translation profile: {profile.name}")
            self.update_cost_estimates()
        except Exception as exc:
            messagebox.showerror("Could not load translation profile", str(exc))

    def preview_translation_segments(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Translation", "Wait for the current run to finish before previewing segments.")
            return
        try:
            config = self.collect_config()
            runner = TranslationRunner(config, self.ui_queue, self.stop_event)
            self.chunk_tree.delete(*self.chunk_tree.get_children())
            self.chunk_rows.clear()
            runner.preview_segments()
        except Exception as exc:
            messagebox.showerror("Could not preview translation segments", str(exc))

    def validate_translation(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Validation", "Wait for the current run to finish before validating.")
            return
        try:
            config = self.collect_config()
        except Exception as exc:
            messagebox.showerror("Check settings", f"One of the settings is invalid:\n{exc}")
            return
        self.start_worker(
            lambda: TranslationRunner(config, self.ui_queue, self.stop_event).validate_current_output(),
            workflow_type="validation",
            config=config,
        )

    def start_provider_test(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Provider test", "Wait for the current run to finish before testing the provider.")
            return
        try:
            config = self.collect_config()
        except Exception as exc:
            messagebox.showerror("Check settings", f"One of the settings is invalid:\n{exc}")
            return

        def run() -> None:
            provider = provider_from_config(config)
            self.ui_queue.put(("log", f"Testing provider: {provider.provider_name} ({provider.base_url})"))
            ok, text = test_connection(OpenAI, provider, min(config.timeout_seconds, 30))
            self.ui_queue.put(("log", text))
            self.ui_queue.put(("status", "Provider test passed" if ok else "Provider test failed"))
            if not ok:
                self.ui_queue.put(("error", text))

        self.start_worker(run, workflow_type="provider_test", config=config)

    def start_model_list(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Model listing", "Wait for the current run to finish before listing models.")
            return
        try:
            config = self.collect_config()
        except Exception as exc:
            messagebox.showerror("Check settings", f"One of the settings is invalid:\n{exc}")
            return

        def run() -> None:
            provider = provider_from_config(config)
            self.ui_queue.put(("log", f"Listing models from: {provider.provider_name} ({provider.base_url})"))
            names = list_models(OpenAI, provider, min(config.timeout_seconds, 30))
            self.ui_queue.put(("log", f"Models returned: {len(names)}"))
            self.ui_queue.put(("models_list", names))
            self.ui_queue.put(("status", "Model list loaded"))

        self.start_worker(run, workflow_type="model_list", config=config)

    def show_model_picker(self, models: list[str]) -> None:
        if not models:
            messagebox.showinfo("Model listing", "The provider returned no model IDs.")
            return
        window = tk.Toplevel(self)
        window.title("Select Model")
        window.configure(bg="#10131a")
        window.geometry("520x420")
        window.transient(self)
        window.columnconfigure(0, weight=1)
        window.rowconfigure(0, weight=1)

        frame = ttk.Frame(window, padding=12)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)
        listbox = tk.Listbox(
            frame,
            bg="#0f1218",
            fg="#eef2f7",
            selectbackground="#2f8f83",
            relief="flat",
            font=("Consolas", 10),
        )
        listbox.grid(row=0, column=0, sticky="nsew")
        for name in models:
            listbox.insert("end", name)

        def use_selected() -> None:
            selection = listbox.curselection()
            if not selection:
                return
            self.vars["model"].set(models[selection[0]])
            self.status_var.set(f"Selected model: {models[selection[0]]}")
            window.destroy()

        ttk.Button(frame, text="Use Selected Model", style="Accent.TButton", command=use_selected).grid(
            row=1, column=0, sticky="ew", pady=(10, 0)
        )

    def load_prompt_source(self) -> None:
        source_name = self.enhancer_source_var.get()
        source_text = self.prompt_text if source_name == "Story Prompt" else self.rewrite_prompt_text
        self.enhancer_source_text.delete("1.0", "end")
        self.enhancer_source_text.insert("1.0", source_text.get("1.0", "end-1c"))

        if source_name == "Story Prompt":
            self.enhancer_mode_var.set("Enhance Story Prompt")
        else:
            self.enhancer_mode_var.set("Enhance Rewrite Prompt")
        self.update_prompt_tool_counts()

    def start_prompt_enhancer(self) -> None:
        if self.worker and self.worker.is_alive():
            messagebox.showinfo("Prompt enhancer", "Wait for the current run to finish before enhancing a prompt.")
            return

        try:
            config = self.collect_config()
        except Exception as exc:
            messagebox.showerror("Check settings", f"One of the settings is invalid:\n{exc}")
            return

        source_prompt = self.enhancer_source_text.get("1.0", "end-1c").strip()
        if not source_prompt:
            self.load_prompt_source()
            source_prompt = self.enhancer_source_text.get("1.0", "end-1c").strip()

        self.enhancer_output_text.delete("1.0", "end")
        self.update_prompt_tool_counts()
        mode = self.enhancer_mode_var.get()
        self.start_worker(
            lambda: PromptEnhancer(config, self.ui_queue, self.stop_event).run(source_prompt, mode),
            workflow_type="prompt_enhancer",
            config=config,
        )

    def apply_enhanced_to_story(self) -> None:
        enhanced = self.enhancer_output_text.get("1.0", "end-1c").strip()
        if not enhanced:
            messagebox.showinfo("Prompt enhancer", "There is no enhanced prompt to apply.")
            return
        self.previous_story_prompt = self.prompt_text.get("1.0", "end-1c")
        self.prompt_text.delete("1.0", "end")
        self.prompt_text.insert("1.0", enhanced)
        self.update_cost_estimates()
        self.update_prompt_tool_counts()
        self.status_var.set("Enhanced prompt applied to Story Prompt")

    def apply_enhanced_to_rewrite(self) -> None:
        enhanced = self.enhancer_output_text.get("1.0", "end-1c").strip()
        if not enhanced:
            messagebox.showinfo("Prompt enhancer", "There is no enhanced prompt to apply.")
            return
        self.previous_rewrite_prompt = self.rewrite_prompt_text.get("1.0", "end-1c")
        self.rewrite_prompt_text.delete("1.0", "end")
        self.rewrite_prompt_text.insert("1.0", enhanced)
        self.update_cost_estimates()
        self.update_prompt_tool_counts()
        self.status_var.set("Enhanced prompt applied to Rewrite Prompt")

    def restore_previous_prompt(self) -> None:
        source_name = self.enhancer_source_var.get()
        if source_name == "Story Prompt":
            if self.previous_story_prompt is None:
                messagebox.showinfo("Prompt enhancer", "No previous story prompt has been saved yet.")
                return
            self.prompt_text.delete("1.0", "end")
            self.prompt_text.insert("1.0", self.previous_story_prompt)
        else:
            if self.previous_rewrite_prompt is None:
                messagebox.showinfo("Prompt enhancer", "No previous rewrite prompt has been saved yet.")
                return
            self.rewrite_prompt_text.delete("1.0", "end")
            self.rewrite_prompt_text.insert("1.0", self.previous_rewrite_prompt)
        self.load_prompt_source()
        self.update_cost_estimates()
        self.status_var.set(f"Previous {source_name.lower()} restored")

    def clear_prompt_tools(self) -> None:
        self.enhancer_source_text.delete("1.0", "end")
        self.enhancer_output_text.delete("1.0", "end")
        self.update_prompt_tool_counts()

    def update_prompt_tool_counts(self) -> None:
        source_words = len(self.enhancer_source_text.get("1.0", "end-1c").split())
        enhanced_words = len(self.enhancer_output_text.get("1.0", "end-1c").split())
        self.enhancer_counts_var.set(f"Source words: {source_words:,} | Enhanced words: {enhanced_words:,}")

    def start_worker(self, target: Callable[[], None], workflow_type: str = "unknown", config: GeneratorConfig | None = None) -> None:
        if self.worker and self.worker.is_alive():
            return

        self.save_settings()
        self.stop_event.clear()
        self.preview_text.delete("1.0", "end")
        self.log_text.delete("1.0", "end")
        self.set_running(True)

        def worker() -> None:
            started = time.monotonic()
            run_id = None
            run_config = config
            if self.history_db is not None and run_config is not None:
                try:
                    run_paths = self.history_run_paths(workflow_type, run_config)
                    run_id = self.history_db.start_run(
                        workflow_type,
                        run_config,
                        title=workflow_type.replace("_", " ").title(),
                        **run_paths,
                    )
                    for role, key in (
                        ("input", "input_file"),
                        ("output", "output_file"),
                        ("report", "report_file"),
                        ("manifest", "manifest_file"),
                    ):
                        self.history_db.add_run_file(run_id, role, run_paths.get(key))
                except Exception as exc:
                    self.ui_queue.put(("log", f"History warning: could not start run record. {exc}"))
                    run_id = None
            try:
                target()
                if self.history_db is not None:
                    self.history_db.finish_run(run_id, "completed", started)
            except KeyboardInterrupt:
                self.ui_queue.put(("log", "Stopped by user. Partial output was saved if streaming had begun."))
                self.ui_queue.put(("status", "Stopped"))
                if self.history_db is not None:
                    self.history_db.finish_run(run_id, "stopped", started, "Stopped by user")
            except Exception as exc:
                error_text = str(exc)
                self.ui_queue.put(("log", f"Error: {error_text}"))
                self.ui_queue.put(("error", error_text))
                self.ui_queue.put(("status", "Error"))
                if self.history_db is not None:
                    self.history_db.finish_run(run_id, "failed", started, error_text)
            finally:
                self.ui_queue.put(("done", ""))

        self.worker = threading.Thread(target=worker, daemon=True)
        self.worker.start()

    def set_running(self, running: bool) -> None:
        state = "disabled" if running else "normal"
        self.story_button.configure(state=state)
        self.rewrite_button.configure(state=state)
        self.retry_button.configure(state=state)
        if hasattr(self, "translation_button"):
            self.translation_button.configure(state=state)
        if hasattr(self, "validation_button"):
            self.validation_button.configure(state=state)
        if hasattr(self, "enhance_button"):
            self.enhance_button.configure(state=state)
        self.stop_button.configure(state="normal" if running else "disabled")
        if running:
            self.progress.start(14)
            self.status_var.set("Starting...")
        else:
            self.progress.stop()

    def start_story(self) -> None:
        try:
            config = self.collect_config()
        except Exception as exc:
            messagebox.showerror("Check settings", f"One of the settings is invalid:\n{exc}")
            return
        self.start_worker(
            lambda: StoryGenerator(config, self.ui_queue, self.stop_event).run(),
            workflow_type="story",
            config=config,
        )

    def start_rewrite(self) -> None:
        try:
            config = self.collect_config()
        except Exception as exc:
            messagebox.showerror("Check settings", f"One of the settings is invalid:\n{exc}")
            return
        self.chunk_tree.delete(*self.chunk_tree.get_children())
        self.chunk_rows.clear()
        self.start_worker(
            lambda: ChunkedRewriter(config, self.ui_queue, self.stop_event).run(),
            workflow_type="rewrite",
            config=config,
        )

    def start_translation(self) -> None:
        try:
            config = self.collect_config()
        except Exception as exc:
            messagebox.showerror("Check settings", f"One of the settings is invalid:\n{exc}")
            return
        self.chunk_tree.delete(*self.chunk_tree.get_children())
        self.chunk_rows.clear()
        self.translation_output_text.delete("1.0", "end")
        self.validation_report_text.delete("1.0", "end")
        self.start_worker(
            lambda: TranslationRunner(config, self.ui_queue, self.stop_event).run(),
            workflow_type="translation",
            config=config,
        )

    def retry_chunk(self) -> None:
        try:
            config = self.collect_config()
        except Exception as exc:
            messagebox.showerror("Check settings", f"One of the settings is invalid:\n{exc}")
            return
        self.start_worker(
            lambda: ChunkedRewriter(config, self.ui_queue, self.stop_event).run(
                retry_chunk=config.rewrite_selected_chunk
            ),
            workflow_type="rewrite",
            config=config,
        )

    def select_chunk_from_table(self, _event: tk.Event) -> None:
        selection = self.chunk_tree.selection()
        if not selection:
            return
        values = self.chunk_tree.item(selection[0], "values")
        if values:
            try:
                self.vars["rewrite_selected_chunk"].set(int(values[0]))
            except (TypeError, ValueError):
                return

    def stop_generation(self) -> None:
        self.stop_event.set()
        self.status_var.set("Stopping after the current stream chunk...")
        self.stop_button.configure(state="disabled")

    def update_chunk_row(self, data: dict[str, Any]) -> None:
        row_key = str(data.get("id", data.get("index", "")))
        if not row_key:
            return
        existing = self.chunk_rows.get(row_key)
        current = {
            "id": row_key,
            "input": "",
            "output": "",
            "ratio": "",
            "finish": "",
            "status": "",
            "validation": "",
            "issues": "",
        }
        if existing:
            values = self.chunk_tree.item(existing, "values")
            current.update(dict(zip(("id", "input", "output", "ratio", "finish", "status", "validation", "issues"), values)))
        if "index" in data and "id" not in data:
            data = {**data, "id": str(data["index"])}
        current.update({key: value for key, value in data.items() if key in current})
        values = (
            current["id"],
            current["input"],
            current["output"],
            current["ratio"],
            current["finish"],
            current["status"],
            current["validation"],
            current["issues"],
        )
        if existing:
            self.chunk_tree.item(existing, values=values)
        else:
            row_id = self.chunk_tree.insert("", "end", values=values)
            self.chunk_rows[row_key] = row_id

    def process_queue(self) -> None:
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "log":
                    self.log_text.insert("end", str(payload) + "\n")
                    self.log_text.see("end")
                elif kind == "preview":
                    self.preview_text.insert("end", str(payload))
                    self.preview_text.see("end")
                elif kind == "translation_preview":
                    self.translation_output_text.insert("end", str(payload))
                    self.translation_output_text.see("end")
                elif kind == "translation_source":
                    self.translation_source_text.delete("1.0", "end")
                    self.translation_source_text.insert("1.0", str(payload))
                elif kind == "translation_output":
                    self.translation_output_text.delete("1.0", "end")
                    self.translation_output_text.insert("1.0", str(payload))
                    self.translation_output_text.see("end")
                elif kind == "validation_report":
                    self.validation_report_text.delete("1.0", "end")
                    self.validation_report_text.insert("1.0", str(payload))
                    self.validation_report_text.see("end")
                elif kind == "models_list":
                    self.show_model_picker(list(payload))
                elif kind == "enhancer_append":
                    self.enhancer_output_text.insert("end", str(payload))
                    self.enhancer_output_text.see("end")
                    self.update_prompt_tool_counts()
                elif kind == "enhancer_done":
                    self.enhancer_output_text.delete("1.0", "end")
                    self.enhancer_output_text.insert("1.0", str(payload))
                    self.update_prompt_tool_counts()
                elif kind == "status":
                    self.status_var.set(str(payload))
                elif kind == "error":
                    messagebox.showerror("Generation failed", str(payload))
                elif kind == "chunk":
                    self.update_chunk_row(payload)
                elif kind == "segment":
                    self.update_chunk_row(payload)
                elif kind == "done":
                    self.set_running(False)
        except queue.Empty:
            pass
        self.after(120, self.process_queue)

    def on_close(self) -> None:
        self.close_history()
        self.destroy()


if __name__ == "__main__":
    app = StoryGeneratorApp()
    app.mainloop()
