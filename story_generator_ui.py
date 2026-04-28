from __future__ import annotations

import ast
import json
import math
import os
import queue
import threading
import time
import tkinter as tk
from dataclasses import asdict, dataclass
from pathlib import Path
from tkinter import filedialog, messagebox, scrolledtext, ttk
from typing import Any, Callable

from legacy_rewrite_adapter import DEFAULT_REWRITE_PROMPT, preclean_text
from providers import (
    PROVIDER_PRESETS,
    PROVIDER_TYPES,
    apply_provider_preset_values,
    list_models,
    provider_from_config,
    test_connection,
)
from history_db import HistoryDB, resolve_history_db_path
from segmentation import SegmentParser
from translation_profiles import (
    builtin_translation_profiles,
    load_translation_profile,
)
from workflows import ChunkedRewriter, PromptEnhancer, StoryGenerator, TranslationRunner

try:
    from openai import OpenAI
except Exception:
    OpenAI = None  # type: ignore[assignment]


APP_DIR = Path(__file__).resolve().parent
APP_NAME = "Long Document LLM Workstation"
APP_VERSION = "2.1.0"
CONFIG_FILE = APP_DIR / "story_generator_ui_config.json"
ORIGINAL_SCRIPT = APP_DIR / "original story deepseek.py"
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


TRANSLATION_SAMPLE_DIR = APP_DIR / "01_test translation" / "translation_stress_test_v9_sanitized_bundle"
DEFAULT_TRANSLATION_INPUT = TRANSLATION_SAMPLE_DIR / "translation_test_source_segments_v9_sanitized.txt"
DEFAULT_TRANSLATION_INSTRUCTIONS = TRANSLATION_SAMPLE_DIR / "translation_test_instructions_v9_sanitized.md"
SAFE_DEFAULT_API_KEY_VALUES = {"", "lm-studio", "ollama", "local"}
PATH_CONFIG_FIELDS = {
    "output_file",
    "history_db_file",
    "rewrite_input_file",
    "rewrite_output_file",
    "rewrite_cleaned_file",
    "rewrite_chunks_dir",
    "translation_input_file",
    "translation_output_file",
    "translation_segments_dir",
    "translation_instruction_file",
    "translation_glossary_file",
    "translation_dnt_file",
    "translation_protected_regex_file",
    "translation_validation_report_file",
}
APP_RELATIVE_PATH_ANCHORS = {
    "01_test translation",
    "app_data",
    "output",
    "outputs",
    "rewrite_chunks",
    "translation_segments",
}
CUSTOM_PROVIDER_PRESET = "Custom OpenAI-Compatible"
PROVIDER_PRESET_CONTROLLED_FIELDS = (
    "provider_type",
    "provider_name",
    "base_url",
    "requires_api_key",
    "default_api_key_value",
    "supports_streaming",
    "supports_response_format",
    "supports_json_schema",
    "supports_tools",
    "supports_reasoning_effort",
    "supports_model_listing",
    "context_window_tokens",
    "provider_max_output_tokens",
)


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


def resolve_path(value: str, default_name: str) -> Path:
    path = Path(str(value).strip() or default_name)
    return path if path.is_absolute() else APP_DIR / path


def resolve_optional_path(value: str) -> Path | None:
    clean = str(value).strip()
    if not clean:
        return None
    path = Path(clean)
    return path if path.is_absolute() else APP_DIR / path


def sanitize_default_api_key_value(value: Any, provider_preset: str = "", requires_api_key: bool = True) -> str:
    clean = str(value or "").strip()
    if clean in SAFE_DEFAULT_API_KEY_VALUES:
        return clean
    preset = PROVIDER_PRESETS.get(provider_preset)
    if preset and not requires_api_key and preset.default_api_key_value in SAFE_DEFAULT_API_KEY_VALUES:
        return preset.default_api_key_value
    return ""


def app_relative_path_tail(path: Path) -> Path | None:
    parts = path.parts
    for index, part in enumerate(parts):
        if part in APP_RELATIVE_PATH_ANCHORS:
            return Path(*parts[index:])
    return None


def portable_config_path(value: Any, fallback: Any) -> str:
    clean = str(value or "").strip()
    if not clean:
        return clean
    path = Path(clean)
    fallback_text = str(fallback or "")

    if not path.is_absolute():
        return clean

    try:
        return str(path.relative_to(APP_DIR))
    except ValueError:
        pass

    if not path.exists():
        tail = app_relative_path_tail(path)
        if tail is not None:
            return str(tail)

    fallback_path = Path(fallback_text) if fallback_text else None
    if fallback_path and path.name == fallback_path.name and not path.exists():
        return fallback_text

    return clean


def sanitize_config_data(data: dict[str, Any], defaults: dict[str, Any]) -> dict[str, Any]:
    sanitized = dict(data)
    sanitized["api_key"] = ""
    sanitized["default_api_key_value"] = sanitize_default_api_key_value(
        sanitized.get("default_api_key_value", ""),
        str(sanitized.get("provider_preset", defaults.get("provider_preset", ""))),
        bool(sanitized.get("requires_api_key", defaults.get("requires_api_key", True))),
    )
    for field in PATH_CONFIG_FIELDS:
        if field in sanitized and field in defaults:
            sanitized[field] = portable_config_path(sanitized[field], defaults[field])
    return sanitized


def load_saved_config() -> GeneratorConfig:
    if not CONFIG_FILE.exists():
        return GeneratorConfig()
    try:
        data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        return GeneratorConfig()

    defaults = asdict(GeneratorConfig())
    safe_data = sanitize_config_data({key: value for key, value in data.items() if key in defaults}, defaults)
    defaults.update(safe_data)
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
        self.api_key_dialog: tk.Toplevel | None = None
        self.api_key_dialog_entry: ttk.Entry | None = None
        self.enter_api_key_button: ttk.Button | None = None
        self.clear_api_key_button: ttk.Button | None = None

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
        style.configure("MutedPanel.TLabel", background="#171b24", foreground="#9ca9b8")
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
        self.add_entry(general, 7, "Session API key", "api_key", show="*")
        self.disable_raw_api_key_entry()
        ttk.Label(general, text="Use Enter API Key.", style="MutedPanel.TLabel").grid(row=7, column=2, sticky="w", pady=5, padx=(6, 0))
        self.add_check(general, 8, "Requires API key", "requires_api_key")
        self.add_entry(general, 9, "Dummy local API key", "default_api_key_value")
        self.api_key_status_var = tk.StringVar()
        ttk.Label(general, text="Key status", style="Panel.TLabel").grid(row=10, column=0, sticky="w", pady=5)
        ttk.Label(general, textvariable=self.api_key_status_var, style="Cost.TLabel").grid(
            row=10, column=1, columnspan=2, sticky="ew", pady=5, padx=(10, 0)
        )
        ttk.Label(
            general,
            text="Session or environment keys are hidden; local providers may use a harmless dummy value.",
            style="MutedPanel.TLabel",
        ).grid(row=11, column=1, columnspan=2, sticky="ew", pady=(0, 5), padx=(10, 0))
        api_key_buttons = ttk.Frame(general, style="Panel.TFrame")
        api_key_buttons.grid(row=12, column=1, columnspan=2, sticky="ew", pady=(0, 5), padx=(10, 0))
        api_key_buttons.columnconfigure(0, weight=1)
        api_key_buttons.columnconfigure(1, weight=1)
        self.enter_api_key_button = ttk.Button(api_key_buttons, text="Enter API Key", command=self.open_enter_api_key_dialog)
        self.enter_api_key_button.grid(
            row=0, column=0, sticky="ew", padx=(0, 4)
        )
        self.clear_api_key_button = ttk.Button(api_key_buttons, text="Clear API Key", command=self.clear_api_key)
        self.clear_api_key_button.grid(
            row=0, column=1, sticky="ew", padx=(4, 0)
        )
        self.add_check(general, 13, "Streaming", "supports_streaming")
        self.add_check(general, 14, "Structured output", "supports_response_format")
        self.add_check(general, 15, "JSON schema", "supports_json_schema")
        self.add_check(general, 16, "Tools", "supports_tools")
        self.add_check(general, 17, "Reasoning controls", "supports_reasoning_effort")
        self.add_check(general, 18, "List models", "supports_model_listing")
        self.add_numeric(general, 19, "Context tokens", "context_window_tokens", 1, 2000000, 1024, is_int=True, use_slider=False)
        self.add_numeric(general, 20, "Max output tokens", "provider_max_output_tokens", 1, 300000, 1024, is_int=True, use_slider=False)
        ttk.Button(general, text="Test Connection", command=self.start_provider_test).grid(row=21, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        ttk.Button(general, text="List Models", command=self.start_model_list).grid(row=22, column=0, columnspan=3, sticky="ew", pady=4)

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
            "api_key",
            "api_key_env",
            "default_api_key_value",
            "requires_api_key",
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
        self.update_api_key_status()
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

    def is_custom_provider_preset(self) -> bool:
        return str(self.vars["provider_preset"].get()) == CUSTOM_PROVIDER_PRESET

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

    def disable_raw_api_key_entry(self) -> None:
        for widget in self.field_widgets.get("api_key", []):
            try:
                widget.configure(state="disabled")
            except tk.TclError:
                pass

    def update_provider_controls(self) -> None:
        provider_type = str(self.vars.get("provider_type", tk.StringVar(value="openrouter")).get())
        is_openrouter = provider_type == "openrouter"
        is_custom_preset = self.is_custom_provider_preset()
        self.set_field_enabled(PROVIDER_PRESET_CONTROLLED_FIELDS, is_custom_preset)
        self.set_field_enabled(
            ("safe_routing", "provider_sort", "allow_fallbacks", "max_prompt_price", "max_completion_price"),
            is_openrouter,
        )
        self.set_field_enabled(
            ("supports_json_schema",),
            is_custom_preset and bool(self.vars["supports_response_format"].get()),
        )
        self.disable_raw_api_key_entry()
        self.schedule_cost_update()
        self.update_api_key_status()

    def is_real_api_key_relevant(self) -> bool:
        provider_type = str(self.vars["provider_type"].get())
        base_url = str(self.vars["base_url"].get()).strip()
        requires_api_key = bool(self.vars["requires_api_key"].get())
        is_local = provider_type in {"lm_studio", "ollama"} or (
            provider_type == "custom_openai_compatible"
            and base_url.startswith(("http://localhost", "http://127.0.0.1", "http://[::1]"))
        )
        return requires_api_key or not is_local

    def update_api_key_status(self) -> None:
        if not hasattr(self, "api_key_status_var"):
            return

        api_key = str(self.vars["api_key"].get()).strip()
        api_key_env = str(self.vars["api_key_env"].get()).strip()
        default_key = str(self.vars["default_api_key_value"].get()).strip()
        requires_api_key = bool(self.vars["requires_api_key"].get())
        env_key_loaded = bool(api_key_env and os.environ.get(api_key_env))
        real_key_relevant = self.is_real_api_key_relevant()

        if api_key:
            status = "API key field has a session value (hidden)"
        elif env_key_loaded:
            status = f"Env key loaded from {api_key_env}"
        elif not real_key_relevant:
            status = "Dummy local key used / No real API key required"
        elif default_key and not requires_api_key:
            status = "Dummy local key used"
        else:
            status = "No key loaded"

        self.api_key_status_var.set(status)
        self.update_api_key_button_states(api_key, real_key_relevant)

    def update_api_key_button_states(self, api_key: str, real_key_relevant: bool) -> None:
        if self.enter_api_key_button is not None:
            self.enter_api_key_button.configure(state="normal" if real_key_relevant else "disabled")
        if self.clear_api_key_button is not None:
            self.clear_api_key_button.configure(state="normal" if api_key else "disabled")

    def open_enter_api_key_dialog(self) -> None:
        if self.api_key_dialog is not None and self.api_key_dialog.winfo_exists():
            self.api_key_dialog.lift()
            return

        dialog = tk.Toplevel(self)
        self.api_key_dialog = dialog
        dialog.title("Enter API Key")
        dialog.transient(self)
        dialog.grab_set()
        dialog.resizable(False, False)
        dialog.configure(bg="#171b24")

        frame = ttk.Frame(dialog, style="Panel.TFrame", padding=16)
        frame.grid(row=0, column=0, sticky="nsew")
        frame.columnconfigure(0, weight=1)

        ttk.Label(
            frame,
            text=(
                "Paste the API key for this app session.\n\n"
                "After saving, the key remains hidden. It is kept only in memory while the app is open. "
                "It is not written to config, logs, or history.\n\n"
                "To replace it later, click Clear API Key, then Enter API Key again."
            ),
            style="Panel.TLabel",
            wraplength=430,
            justify="left",
        ).grid(row=0, column=0, sticky="ew", pady=(0, 12))

        api_key_value = tk.StringVar()
        entry = ttk.Entry(frame, textvariable=api_key_value, show="*", width=56)
        self.api_key_dialog_entry = entry
        entry.grid(row=1, column=0, sticky="ew", pady=(0, 12))

        buttons = ttk.Frame(frame, style="Panel.TFrame")
        buttons.grid(row=2, column=0, sticky="ew")
        buttons.columnconfigure(0, weight=1)
        buttons.columnconfigure(1, weight=1)

        def close_dialog() -> None:
            self.api_key_dialog_entry = None
            self.api_key_dialog = None
            dialog.destroy()

        def confirm() -> None:
            key = api_key_value.get().strip()
            if not key:
                messagebox.showinfo("API key", "Paste an API key before saving.", parent=dialog)
                return
            self.vars["api_key"].set(key)
            self.update_api_key_status()
            self.status_var.set("API key loaded for this app session")
            close_dialog()

        ttk.Button(buttons, text="Cancel", command=close_dialog).grid(row=0, column=0, sticky="ew", padx=(0, 5))
        ttk.Button(buttons, text="Save Key", style="Accent.TButton", command=confirm).grid(
            row=0, column=1, sticky="ew", padx=(5, 0)
        )
        dialog.bind("<Escape>", lambda _event: close_dialog())
        dialog.bind("<Return>", lambda _event: confirm())
        dialog.protocol("WM_DELETE_WINDOW", close_dialog)
        entry.focus_set()
        self.wait_visibility(dialog)

    def clear_api_key(self) -> None:
        self.vars["api_key"].set("")
        self.update_api_key_status()
        self.status_var.set("Session API key cleared")

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
            data = sanitize_config_data(asdict(config), asdict(GeneratorConfig()))
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
