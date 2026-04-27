from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from providers import (
    PROVIDER_PRESETS,
    ProviderProfile,
    build_client,
    chat_completion_kwargs,
    list_models,
    response_to_stream_chunks,
    test_connection,
)


class FakeOpenAI:
    def __init__(self, base_url: str, api_key: str, timeout: int) -> None:
        self.base_url = base_url
        self.api_key = api_key
        self.timeout = timeout
        self.models = self.Models()
        self.chat = self.Chat()

    class Models:
        def list(self) -> SimpleNamespace:
            return SimpleNamespace(
                data=[
                    SimpleNamespace(id="local-alpha"),
                    SimpleNamespace(id="local-beta"),
                ]
            )

    class Chat:
        def __init__(self) -> None:
            self.completions = self.Completions()

        class Completions:
            def create(self, **_kwargs: object) -> SimpleNamespace:
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(content="OK"),
                            finish_reason="stop",
                        )
                    ]
                )


class FakeOpenAINoModels(FakeOpenAI):
    class Models:
        def list(self) -> SimpleNamespace:
            raise RuntimeError("models endpoint unavailable")


def fake_config(**overrides: object) -> SimpleNamespace:
    values = {
        "provider_sort": "price",
        "allow_fallbacks": False,
        "max_prompt_price": 0.14,
        "max_completion_price": 0.28,
        "safe_routing": True,
        "supports_reasoning_effort": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def assert_request_bodies() -> None:
    messages = [{"role": "user", "content": "Ping"}]
    config = fake_config()

    openrouter_kwargs = chat_completion_kwargs(
        config,
        PROVIDER_PRESETS["OpenRouter"],
        messages,
        temperature=0.2,
        top_p=0.9,
        max_tokens=100,
    )
    assert "extra_body" in openrouter_kwargs
    assert "provider" in openrouter_kwargs["extra_body"]

    for preset_name in ("LM Studio Local", "Ollama Local", "Custom OpenAI-Compatible"):
        kwargs = chat_completion_kwargs(
            config,
            PROVIDER_PRESETS[preset_name],
            messages,
            temperature=0.2,
            top_p=0.9,
            max_tokens=100,
        )
        assert "extra_body" not in kwargs, preset_name

    no_stream = replace(PROVIDER_PRESETS["LM Studio Local"], supports_streaming=False, max_output_tokens=8)
    kwargs = chat_completion_kwargs(config, no_stream, messages, 0.2, 0.9, max_tokens=100, stream=True)
    assert kwargs["stream"] is False
    assert kwargs["max_tokens"] == 8


def assert_client_keys() -> None:
    lm_client = build_client(FakeOpenAI, PROVIDER_PRESETS["LM Studio Local"], 5)
    assert lm_client.base_url == "http://localhost:1234/v1"
    assert lm_client.api_key == "lm-studio"

    ollama_client = build_client(FakeOpenAI, PROVIDER_PRESETS["Ollama Local"], 5)
    assert ollama_client.base_url == "http://localhost:11434/v1"
    assert ollama_client.api_key == "ollama"

    missing_key_provider = ProviderProfile(
        provider_name="Needs Key",
        provider_type="openai_compatible_cloud",
        base_url="https://example.invalid/v1",
        api_key="",
        api_key_env="__NO_SUCH_ENV_VAR_FOR_PROVIDER_SMOKE_TEST__",
        requires_api_key=True,
    )
    try:
        build_client(FakeOpenAI, missing_key_provider, 5)
    except RuntimeError as exc:
        assert "Missing API key" in str(exc)
    else:
        raise AssertionError("Expected missing API key failure")


def assert_model_and_connection_tests() -> None:
    names = list_models(FakeOpenAI, PROVIDER_PRESETS["LM Studio Local"], 5)
    assert names == ["local-alpha", "local-beta"]

    ok, text = test_connection(FakeOpenAI, PROVIDER_PRESETS["LM Studio Local"], 5)
    assert ok is True
    assert "Models endpoint returned 2" in text

    ok, text = test_connection(FakeOpenAINoModels, PROVIDER_PRESETS["LM Studio Local"], 5)
    assert ok is True
    assert "tiny chat test passed" in text


def assert_response_adapter() -> None:
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="OK"),
                finish_reason="stop",
            )
        ]
    )
    chunks = response_to_stream_chunks(response)
    assert len(chunks) == 1
    assert chunks[0].choices[0].delta.content == "OK"
    assert chunks[0].choices[0].finish_reason == "stop"


def main() -> None:
    assert_request_bodies()
    assert_client_keys()
    assert_model_and_connection_tests()
    assert_response_adapter()
    print("provider smoke tests passed")


if __name__ == "__main__":
    main()
