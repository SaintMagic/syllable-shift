from __future__ import annotations

from types import SimpleNamespace

from config_models import (
    AppConfig,
    ProjectConfig,
    generator_to_app_config,
    generator_to_project_config,
    merge_app_project_to_generator,
)
from story_generator_ui import GeneratorConfig


APP_FIELDS = {
    "history_enabled",
    "history_db_file",
    "provider_preset",
    "provider_name",
    "provider_type",
    "model_preset",
    "model",
    "base_url",
    "api_key_env",
    "supports_streaming",
    "supports_json_schema",
    "supports_response_format",
    "supports_tools",
    "supports_reasoning_effort",
    "requires_api_key",
    "default_api_key_value",
    "context_window_tokens",
    "provider_max_output_tokens",
    "supports_model_listing",
    "provider_notes",
    "safe_routing",
    "provider_sort",
    "allow_fallbacks",
    "max_prompt_price",
    "max_completion_price",
    "max_retries",
    "timeout_seconds",
}

PROJECT_FIELDS = {
    "output_file",
    "temperature",
    "top_p",
    "max_tokens_per_call",
    "max_continuations",
    "continue_marker",
    "system_prompt",
    "continuation_system_prompt",
    "story_target_min_words",
    "story_target_max_words",
    "story_prompt",
    "rewrite_input_file",
    "rewrite_output_file",
    "rewrite_cleaned_file",
    "rewrite_chunks_dir",
    "rewrite_chunk_words",
    "rewrite_temperature",
    "rewrite_top_p",
    "rewrite_max_tokens_per_call",
    "rewrite_pause_seconds",
    "rewrite_min_ratio",
    "rewrite_max_ratio",
    "rewrite_target_min_ratio",
    "rewrite_target_max_ratio",
    "rewrite_selected_chunk",
    "rewrite_system_prompt",
    "enhancer_model",
    "enhancer_temperature",
    "translation_input_file",
    "translation_output_file",
    "translation_segments_dir",
    "translation_source_language",
    "translation_target_language",
    "translation_register_mode",
    "translation_instruction_file",
    "translation_instruction_text",
    "translation_glossary_file",
    "translation_dnt_file",
    "translation_protected_regex_file",
    "translation_segment_delimiter_style",
    "translation_custom_delimiter_regex",
    "translation_chunk_segments",
    "translation_max_tokens_per_call",
    "translation_temperature",
    "translation_top_p",
    "translation_pause_seconds",
    "translation_validate_after_run",
    "translation_validator_profile",
    "translation_validation_report_file",
    "translation_grouped_report",
    "translation_save_json_report",
}


def assert_fields_equal(source: object, target: object, fields: set[str]) -> None:
    for field in fields:
        assert getattr(target, field) == getattr(source, field), field


def test_generator_to_app_config() -> None:
    source = GeneratorConfig(
        history_enabled=False,
        history_db_file="app_data/custom.sqlite3",
        provider_preset="LM Studio Local",
        provider_name="LM Studio Local",
        provider_type="lm_studio",
        model_preset="Custom",
        model="local-model-id",
        base_url="http://localhost:1234/v1",
        api_key="sk-raw-secret",
        api_key_env="",
        requires_api_key=False,
        default_api_key_value="lm-studio",
        safe_routing=False,
        provider_sort="latency",
        allow_fallbacks=True,
        max_prompt_price=0.25,
        max_completion_price=0.75,
        max_retries=3,
        timeout_seconds=120,
    )
    app = generator_to_app_config(source)

    assert isinstance(app, AppConfig)
    assert_fields_equal(source, app, APP_FIELDS)
    assert not hasattr(app, "api_key")


def test_generator_to_project_config() -> None:
    source = GeneratorConfig(
        output_file="story-output.md",
        temperature=0.66,
        top_p=0.88,
        max_tokens_per_call=12345,
        max_continuations=4,
        continue_marker="[NEXT]",
        system_prompt="Synthetic system prompt.",
        continuation_system_prompt="Synthetic continuation prompt.",
        story_target_min_words=111,
        story_target_max_words=222,
        story_prompt="Synthetic story prompt.",
        rewrite_input_file="input.md",
        rewrite_output_file="rewritten.md",
        rewrite_cleaned_file="cleaned.md",
        rewrite_chunks_dir="chunks",
        rewrite_chunk_words=900,
        rewrite_temperature=0.44,
        rewrite_top_p=0.77,
        rewrite_max_tokens_per_call=7000,
        rewrite_pause_seconds=1,
        rewrite_min_ratio=0.80,
        rewrite_max_ratio=1.20,
        rewrite_target_min_ratio=0.95,
        rewrite_target_max_ratio=1.05,
        rewrite_selected_chunk=2,
        rewrite_system_prompt="Synthetic rewrite prompt.",
        enhancer_model="enhancer-model",
        enhancer_temperature=0.11,
        translation_input_file="translation-source.md",
        translation_output_file="translation-output.md",
        translation_segments_dir="translation-segments",
        translation_source_language="English",
        translation_target_language="Spanish",
        translation_register_mode="Formal",
        translation_instruction_file="instructions.md",
        translation_instruction_text="Synthetic translation instructions.",
        translation_glossary_file="glossary.csv",
        translation_dnt_file="dnt.txt",
        translation_protected_regex_file="protected.txt",
        translation_segment_delimiter_style="Markdown Headings",
        translation_custom_delimiter_regex="^##",
        translation_chunk_segments=3,
        translation_max_tokens_per_call=9000,
        translation_temperature=0.12,
        translation_top_p=0.93,
        translation_pause_seconds=4,
        translation_validate_after_run=False,
        translation_validator_profile="General",
        translation_validation_report_file="report.md",
        translation_grouped_report=False,
        translation_save_json_report=True,
    )
    project = generator_to_project_config(source)

    assert isinstance(project, ProjectConfig)
    assert_fields_equal(source, project, PROJECT_FIELDS)
    assert not hasattr(project, "api_key")


def test_roundtrip_to_generator_config() -> None:
    source = GeneratorConfig(
        api_key="sk-raw-secret",
        provider_type="ollama",
        provider_name="Ollama Local",
        default_api_key_value="ollama",
        model="llama3.1",
        output_file="roundtrip-story.md",
        translation_target_language="German",
    )
    app = generator_to_app_config(source)
    project = generator_to_project_config(source)
    merged = merge_app_project_to_generator(app, project, source)

    assert isinstance(merged, GeneratorConfig)
    assert merged.api_key == ""
    assert merged.default_api_key_value == "ollama"
    assert merged.provider_type == source.provider_type
    assert merged.provider_name == source.provider_name
    assert merged.model == source.model
    assert merged.output_file == source.output_file
    assert merged.translation_target_language == source.translation_target_language


def test_secret_default_key_values_are_stripped() -> None:
    source = GeneratorConfig(
        api_key="sk-raw-secret",
        provider_preset="OpenRouter",
        requires_api_key=True,
        default_api_key_value="sk-should-not-survive",
    )
    app = generator_to_app_config(source)
    project = generator_to_project_config(source)
    merged = merge_app_project_to_generator(app, project, source)

    assert not hasattr(app, "api_key")
    assert app.default_api_key_value == ""
    assert merged.api_key == ""
    assert merged.default_api_key_value == ""


def test_safe_dummy_default_key_values_are_allowed() -> None:
    for dummy in ("", "lm-studio", "ollama", "local"):
        source = GeneratorConfig(default_api_key_value=dummy, requires_api_key=False)
        app = generator_to_app_config(source)
        merged = merge_app_project_to_generator(app, generator_to_project_config(source), source)

        assert app.default_api_key_value == dummy
        assert merged.default_api_key_value == dummy


def test_missing_default_fields_are_safe() -> None:
    partial = SimpleNamespace(
        provider_type="custom_openai_compatible",
        model="custom-model",
        output_file="partial-story.md",
        unknown_future_field="ignored",
    )
    app = generator_to_app_config(partial)
    project = generator_to_project_config(partial)
    merged = merge_app_project_to_generator(app, project)

    assert app.provider_type == "custom_openai_compatible"
    assert app.model == "custom-model"
    assert app.provider_name == AppConfig().provider_name
    assert project.output_file == "partial-story.md"
    assert project.translation_output_file == ProjectConfig().translation_output_file
    assert merged.provider_type == "custom_openai_compatible"
    assert merged.output_file == "partial-story.md"
    assert not hasattr(merged, "unknown_future_field")
    assert not hasattr(merged, "api_key")


def main() -> None:
    test_generator_to_app_config()
    test_generator_to_project_config()
    test_roundtrip_to_generator_config()
    test_secret_default_key_values_are_stripped()
    test_safe_dummy_default_key_values_are_allowed()
    test_missing_default_fields_are_safe()
    print("config model tests passed")


if __name__ == "__main__":
    main()
