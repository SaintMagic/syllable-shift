from __future__ import annotations

import os
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any


PROVIDER_TYPES = (
    "openrouter",
    "openai_compatible_cloud",
    "lm_studio",
    "ollama",
    "custom_openai_compatible",
)

LOCAL_PROVIDER_TYPES = {"lm_studio", "ollama"}


@dataclass
class ProviderProfile:
    provider_name: str = "OpenRouter"
    provider_type: str = "openrouter"
    base_url: str = "https://openrouter.ai/api/v1"
    api_key: str = ""
    api_key_env: str = "OPENROUTER_API_KEY"
    model: str = "deepseek/deepseek-v4-flash"
    supports_streaming: bool = True
    supports_json_schema: bool = False
    supports_response_format: bool = False
    supports_tools: bool = False
    supports_reasoning_effort: bool = False
    requires_api_key: bool = True
    default_api_key_value: str = ""
    context_window_tokens: int = 131072
    max_output_tokens: int = 120000
    supports_model_listing: bool = True
    notes: str = ""

    @property
    def is_openrouter(self) -> bool:
        return self.provider_type == "openrouter"

    @property
    def is_local(self) -> bool:
        return self.provider_type in LOCAL_PROVIDER_TYPES or (
            self.provider_type == "custom_openai_compatible"
            and self.base_url.startswith(("http://localhost", "http://127.0.0.1", "http://[::1]"))
        )

    def resolved_api_key(self) -> str:
        return self.api_key.strip() or os.environ.get(self.api_key_env.strip(), "") or self.default_api_key_value


# LM Studio docs list OpenAI-compatible /v1/models and /v1/chat/completions at the
# usual local server port 1234. Ollama docs show OpenAI-compatible /v1/chat/completions
# at localhost:11434/v1 with an ignored API key.
PROVIDER_PRESETS: dict[str, ProviderProfile] = {
    "OpenRouter": ProviderProfile(
        provider_name="OpenRouter",
        provider_type="openrouter",
        base_url="https://openrouter.ai/api/v1",
        api_key_env="OPENROUTER_API_KEY",
        model="deepseek/deepseek-v4-flash",
        requires_api_key=True,
        context_window_tokens=131072,
        max_output_tokens=120000,
        notes="Cloud OpenAI-compatible endpoint with OpenRouter routing/cost controls.",
    ),
    "LM Studio Local": ProviderProfile(
        provider_name="LM Studio Local",
        provider_type="lm_studio",
        base_url="http://localhost:1234/v1",
        api_key="",
        api_key_env="",
        model="local-model",
        requires_api_key=False,
        default_api_key_value="lm-studio",
        context_window_tokens=32768,
        max_output_tokens=8192,
        supports_response_format=False,
        supports_json_schema=False,
        notes="Local OpenAI-compatible endpoint. Model ID depends on the loaded LM Studio model.",
    ),
    "Ollama Local": ProviderProfile(
        provider_name="Ollama Local",
        provider_type="ollama",
        base_url="http://localhost:11434/v1",
        api_key="",
        api_key_env="",
        model="llama3.1",
        requires_api_key=False,
        default_api_key_value="ollama",
        context_window_tokens=32768,
        max_output_tokens=8192,
        supports_response_format=False,
        supports_json_schema=False,
        notes="Local OpenAI-compatible endpoint. Model ID must match an installed Ollama model.",
    ),
    "Custom OpenAI-Compatible": ProviderProfile(
        provider_name="Custom OpenAI-Compatible",
        provider_type="custom_openai_compatible",
        base_url="http://localhost:8000/v1",
        api_key="",
        api_key_env="",
        model="custom-model",
        requires_api_key=False,
        default_api_key_value="local",
        context_window_tokens=32768,
        max_output_tokens=8192,
        notes="Editable OpenAI-compatible endpoint.",
    ),
}


def provider_from_config(config: Any) -> ProviderProfile:
    return ProviderProfile(
        provider_name=str(getattr(config, "provider_name", "OpenRouter")),
        provider_type=str(getattr(config, "provider_type", "openrouter")),
        base_url=str(getattr(config, "base_url", "https://openrouter.ai/api/v1")),
        api_key=str(getattr(config, "api_key", "")),
        api_key_env=str(getattr(config, "api_key_env", "OPENROUTER_API_KEY")),
        model=str(getattr(config, "model", "deepseek/deepseek-v4-flash")),
        supports_streaming=bool(getattr(config, "supports_streaming", True)),
        supports_json_schema=bool(getattr(config, "supports_json_schema", False)),
        supports_response_format=bool(getattr(config, "supports_response_format", False)),
        supports_tools=bool(getattr(config, "supports_tools", False)),
        supports_reasoning_effort=bool(getattr(config, "supports_reasoning_effort", False)),
        requires_api_key=bool(getattr(config, "requires_api_key", True)),
        default_api_key_value=str(getattr(config, "default_api_key_value", "")),
        context_window_tokens=int(getattr(config, "context_window_tokens", 131072) or 131072),
        max_output_tokens=int(getattr(config, "provider_max_output_tokens", 120000) or 120000),
        supports_model_listing=bool(getattr(config, "supports_model_listing", True)),
        notes=str(getattr(config, "provider_notes", "")),
    )


def apply_provider_preset_values(values: dict[str, Any], preset_name: str) -> None:
    preset = PROVIDER_PRESETS.get(preset_name)
    if not preset:
        return
    values.update(
        {
            "provider_name": preset.provider_name,
            "provider_type": preset.provider_type,
            "base_url": preset.base_url,
            "api_key": preset.api_key,
            "api_key_env": preset.api_key_env,
            "model": preset.model,
            "supports_streaming": preset.supports_streaming,
            "supports_json_schema": preset.supports_json_schema,
            "supports_response_format": preset.supports_response_format,
            "supports_tools": preset.supports_tools,
            "supports_reasoning_effort": preset.supports_reasoning_effort,
            "requires_api_key": preset.requires_api_key,
            "default_api_key_value": preset.default_api_key_value,
            "context_window_tokens": preset.context_window_tokens,
            "provider_max_output_tokens": preset.max_output_tokens,
            "supports_model_listing": preset.supports_model_listing,
            "provider_notes": preset.notes,
        }
    )


def build_client(openai_cls: Any, profile: ProviderProfile, timeout_seconds: int) -> Any:
    if openai_cls is None:
        raise RuntimeError("The openai package is not installed. Run: pip install openai")

    api_key = profile.resolved_api_key()
    if profile.requires_api_key and not api_key:
        env_hint = f" or set {profile.api_key_env}" if profile.api_key_env else ""
        raise RuntimeError(f"Missing API key for {profile.provider_name}. Enter one in the UI{env_hint}.")
    if not api_key:
        api_key = profile.default_api_key_value or "local"
    return openai_cls(base_url=profile.base_url, api_key=api_key, timeout=timeout_seconds)


def openrouter_extra_body(config: Any, profile: ProviderProfile) -> dict[str, Any] | None:
    if not profile.is_openrouter:
        return None
    body: dict[str, Any] = {}
    if bool(getattr(config, "supports_reasoning_effort", False)):
        body["reasoning"] = {"enabled": False}
    if bool(getattr(config, "safe_routing", True)):
        body["provider"] = {
            "sort": str(getattr(config, "provider_sort", "price")),
            "allow_fallbacks": bool(getattr(config, "allow_fallbacks", False)),
            "max_price": {
                "prompt": float(getattr(config, "max_prompt_price", 0.0)),
                "completion": float(getattr(config, "max_completion_price", 0.0)),
            },
        }
    return body or None


def chat_completion_kwargs(
    config: Any,
    profile: ProviderProfile,
    messages: list[dict[str, str]],
    temperature: float,
    top_p: float,
    max_tokens: int,
    model: str | None = None,
    stream: bool = True,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": model or profile.model,
        "messages": messages,
        "temperature": temperature,
        "top_p": top_p,
        "max_tokens": min(max_tokens, max(1, profile.max_output_tokens)),
        "stream": stream and profile.supports_streaming,
    }
    extra_body = openrouter_extra_body(config, profile)
    if extra_body:
        kwargs["extra_body"] = extra_body
    return kwargs


def response_to_stream_chunks(response: Any) -> list[Any]:
    choice = response.choices[0] if getattr(response, "choices", None) else None
    if choice is None:
        return []
    message = getattr(choice, "message", None)
    content = getattr(message, "content", "") if message is not None else ""
    finish_reason = getattr(choice, "finish_reason", None)
    return [SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content), finish_reason=finish_reason)])]


def list_models(openai_cls: Any, profile: ProviderProfile, timeout_seconds: int) -> list[str]:
    if not profile.supports_model_listing:
        raise RuntimeError("Model listing not supported by this provider.")
    client = build_client(openai_cls, profile, timeout_seconds)
    models = client.models.list()
    data = getattr(models, "data", []) or []
    names = sorted(str(getattr(item, "id", "")) for item in data if getattr(item, "id", ""))
    if not names:
        raise RuntimeError("The provider returned no model IDs.")
    return names


def test_connection(openai_cls: Any, profile: ProviderProfile, timeout_seconds: int) -> tuple[bool, str]:
    try:
        try:
            names = list_models(openai_cls, profile, min(timeout_seconds, 30))
            return True, f"Connected to {profile.provider_name}. Models endpoint returned {len(names)} model(s)."
        except Exception as model_exc:
            client = build_client(openai_cls, profile, min(timeout_seconds, 30))
            response = client.chat.completions.create(
                model=profile.model,
                messages=[
                    {"role": "system", "content": "You are a test responder."},
                    {"role": "user", "content": "Reply with OK only."},
                ],
                temperature=0,
                max_tokens=4,
                stream=False,
            )
            content = response.choices[0].message.content if response.choices else ""
            if "OK" not in str(content).upper():
                return True, f"Connected to {profile.provider_name}, but test response was unexpected: {content!r}"
            return True, f"Connected to {profile.provider_name}. Models endpoint failed, tiny chat test passed. ({model_exc})"
    except Exception as exc:
        return False, f"{profile.provider_name} connection failed: {exc}"
