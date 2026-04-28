from __future__ import annotations

from dataclasses import asdict, dataclass, fields, is_dataclass, replace
from types import SimpleNamespace
from typing import Any, TypeVar


SAFE_DEFAULT_API_KEY_VALUES = {"", "lm-studio", "ollama", "local"}
SECRET_FIELDS = {"api_key"}


@dataclass
class AppConfig:
    history_enabled: bool = True
    history_db_file: str = "app_data/workstation_history.sqlite3"
    provider_preset: str = "OpenRouter"
    provider_name: str = "OpenRouter"
    provider_type: str = "openrouter"
    model_preset: str = "DeepSeek V4 Flash cheap"
    model: str = "deepseek/deepseek-v4-flash"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key_env: str = "OPENROUTER_API_KEY"
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
    max_retries: int = 8
    timeout_seconds: int = 7200


@dataclass
class ProjectConfig:
    output_file: str = "deepseek_original_novella.md"
    temperature: float = 0.78
    top_p: float = 0.92
    max_tokens_per_call: int = 120000
    max_continuations: int = 5
    continue_marker: str = "[STORY_CONTINUES]"
    system_prompt: str = ""
    continuation_system_prompt: str = ""
    story_target_min_words: int = 25000
    story_target_max_words: int = 40000
    story_prompt: str = ""
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
    rewrite_system_prompt: str = ""
    enhancer_model: str = "deepseek/deepseek-v4-flash"
    enhancer_temperature: float = 0.25
    translation_input_file: str = ""
    translation_output_file: str = "translation_output.md"
    translation_segments_dir: str = "translation_segments"
    translation_source_language: str = "English"
    translation_target_language: str = ""
    translation_register_mode: str = "Professional/staff-facing"
    translation_instruction_file: str = ""
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


T = TypeVar("T")


def _field_names(cls: type[Any]) -> set[str]:
    return {field.name for field in fields(cls)}


def _object_values(source: Any) -> dict[str, Any]:
    if is_dataclass(source):
        return asdict(source)
    if isinstance(source, dict):
        return dict(source)
    return dict(vars(source))


def sanitize_default_api_key_value(value: Any) -> str:
    clean = str(value or "").strip()
    return clean if clean in SAFE_DEFAULT_API_KEY_VALUES else ""


def _pick(source: Any, target_cls: type[T]) -> T:
    values = _object_values(source)
    for key in SECRET_FIELDS:
        values.pop(key, None)
    if "default_api_key_value" in values:
        values["default_api_key_value"] = sanitize_default_api_key_value(values["default_api_key_value"])
    allowed = _field_names(target_cls)
    return target_cls(**{key: value for key, value in values.items() if key in allowed})


def generator_to_app_config(generator_config: Any) -> AppConfig:
    return _pick(generator_config, AppConfig)


def generator_to_project_config(generator_config: Any) -> ProjectConfig:
    return _pick(generator_config, ProjectConfig)


def merge_app_project_to_generator(
    app_config: AppConfig,
    project_config: ProjectConfig,
    generator_config: Any | None = None,
) -> Any:
    values: dict[str, Any] = {}
    if generator_config is not None:
        values.update(_object_values(generator_config))
    values.update(asdict(app_config))
    values.update(asdict(project_config))
    for key in SECRET_FIELDS:
        values[key] = ""
    if "default_api_key_value" in values:
        values["default_api_key_value"] = sanitize_default_api_key_value(values["default_api_key_value"])

    if generator_config is None:
        return SimpleNamespace(**values)
    if is_dataclass(generator_config):
        allowed = _field_names(type(generator_config))
        return replace(generator_config, **{key: value for key, value in values.items() if key in allowed})

    for key, value in values.items():
        if hasattr(generator_config, key):
            setattr(generator_config, key, value)
    return generator_config
