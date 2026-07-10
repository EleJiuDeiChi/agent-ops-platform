from __future__ import annotations

import json

import pytest


def test_provider_payload_redacts_configured_secrets_and_common_pii(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AIOPS_ENV", "test")
    monkeypatch.setenv("AIOPS_SESSION_SECRET", "session-secret-for-redaction")
    monkeypatch.setenv("AIOPS_LLM_MODE", "openai_compatible")
    monkeypatch.setenv("AIOPS_LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("AIOPS_LLM_MODEL", "capture-model")
    monkeypatch.setenv("AIOPS_LLM_API_KEY", "provider-api-key-secret")
    monkeypatch.setenv("AIOPS_LLM_RETRIES", "0")

    captured: dict = {}

    class Response:
        status_code = 200
        text = "ok"

        @staticmethod
        def json() -> dict:
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return Response()

    monkeypatch.setattr("app.ai.providers.httpx.post", fake_post)
    from app.ai.providers import OpenAICompatibleChatProvider

    messages = [
        {
            "role": "user",
            "content": (
                "联系 alice@example.com 或 13800138000，身份证 11010519491231002X；"
                "password=hunter2；session-secret-for-redaction；provider-api-key-secret；"
                "mysql://root:supersecret@127.0.0.1/prod；"
                "Authorization: Basic dXNlcjpwYXNzd29yZA=="
            ),
        },
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "content": json.dumps(
                {
                    "output_summary": "Bearer abcdefghijklmnop",
                    "error": "token=tool-secret-value",
                }
            ),
        },
    ]
    OpenAICompatibleChatProvider().chat_completion(messages)

    serialized = json.dumps(captured["json"], ensure_ascii=False)
    for forbidden in (
        "alice@example.com",
        "13800138000",
        "11010519491231002X",
        "hunter2",
        "session-secret-for-redaction",
        "provider-api-key-secret",
        "abcdefghijklmnop",
        "tool-secret-value",
        "root:supersecret",
        "dXNlcjpwYXNzd29yZA==",
    ):
        assert forbidden not in serialized
    assert "[REDACTED_SECRET]" in serialized
    assert "[REDACTED_PII]" in serialized
    assert "alice@example.com" in messages[0]["content"]


def test_provider_error_body_is_redacted_before_it_can_reach_events(monkeypatch) -> None:
    monkeypatch.setenv("AIOPS_ENV", "test")
    monkeypatch.setenv("AIOPS_LLM_MODE", "openai_compatible")
    monkeypatch.setenv("AIOPS_LLM_BASE_URL", "https://llm.example.test/v1")
    monkeypatch.setenv("AIOPS_LLM_MODEL", "capture-model")
    monkeypatch.setenv("AIOPS_LLM_API_KEY", "provider-api-key-secret")
    monkeypatch.setenv("AIOPS_LLM_RETRIES", "0")

    class Response:
        status_code = 400
        text = (
            "mysql://root:supersecret@127.0.0.1/prod "
            "Authorization: Basic dXNlcjpwYXNzd29yZA=="
        )

    monkeypatch.setattr("app.ai.providers.httpx.post", lambda *args, **kwargs: Response())
    from app.ai.providers import OpenAICompatibleChatProvider, LLMProviderError

    with pytest.raises(LLMProviderError) as captured:
        OpenAICompatibleChatProvider().chat_completion([{"role": "user", "content": "safe"}])
    message = str(captured.value)
    assert "supersecret" not in message
    assert "dXNlcjpwYXNzd29yZA==" not in message
    assert "[REDACTED_SECRET]" in message


@pytest.mark.parametrize(
    ("provider_id", "base_url", "expected_reasoning"),
    [
        ("deepseek", "https://api.deepseek.com", True),
        ("moonshot", "https://api.moonshot.ai/v1", False),
        ("zhipu", "https://open.bigmodel.cn/api/paas/v4", True),
    ],
)
def test_built_in_provider_payload_adapts_reasoning_fields(
    monkeypatch,
    provider_id: str,
    base_url: str,
    expected_reasoning: bool,
) -> None:
    monkeypatch.setenv("AIOPS_ENV", "test")
    monkeypatch.setenv("AIOPS_LLM_MODE", provider_id)
    monkeypatch.setenv("AIOPS_LLM_BASE_URL", base_url)
    monkeypatch.setenv("AIOPS_LLM_MODEL", "provider-model")
    monkeypatch.setenv("AIOPS_LLM_API_KEY", "provider-api-key-secret")
    monkeypatch.setenv("AIOPS_LLM_RETRIES", "0")
    captured: dict = {}

    class Response:
        status_code = 200
        text = "ok"

        @staticmethod
        def json() -> dict:
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]
        return Response()

    monkeypatch.setattr("app.ai.providers.httpx.post", fake_post)
    from app.ai.providers import OpenAICompatibleChatProvider

    OpenAICompatibleChatProvider().chat_completion([{"role": "user", "content": "safe"}])

    assert captured["url"] == f"{base_url}/chat/completions"
    assert captured["json"]["thinking"] == {"type": "enabled"}
    assert ("reasoning_effort" in captured["json"]) is expected_reasoning


def test_provider_registry_declares_required_contract_capabilities() -> None:
    from app.ai.provider_profiles import PROVIDER_PROFILES

    expected_cache_formats = {
        "deepseek": "deepseek_cache_fields",
        "moonshot": "kimi_cached_tokens",
        "zhipu": "prompt_tokens_details",
    }
    for provider_id in ("deepseek", "moonshot", "zhipu"):
        profile = PROVIDER_PROFILES[provider_id]
        assert profile.production_base_url
        assert profile.candidate_model
        assert profile.api_key_variable
        assert profile.supports_streaming is True
        assert profile.supports_tool_calls is True
        assert profile.reports_usage is True
        assert profile.cache_usage_format == expected_cache_formats[provider_id]


@pytest.mark.parametrize(
    ("provider_id", "base_url"),
    [
        ("moonshot", "https://api.moonshot.ai/v1"),
        ("zhipu", "https://open.bigmodel.cn/api/paas/v4"),
    ],
)
def test_provider_rejects_unsupported_named_tool_choice(
    monkeypatch,
    provider_id: str,
    base_url: str,
) -> None:
    monkeypatch.setenv("AIOPS_ENV", "test")
    monkeypatch.setenv("AIOPS_LLM_MODE", provider_id)
    monkeypatch.setenv("AIOPS_LLM_BASE_URL", base_url)
    monkeypatch.setenv("AIOPS_LLM_MODEL", "provider-model")
    monkeypatch.setenv("AIOPS_LLM_API_KEY", "provider-api-key-secret")

    from app.ai.providers import LLMProviderError, OpenAICompatibleChatProvider

    with pytest.raises(LLMProviderError, match="named tool choice"):
        OpenAICompatibleChatProvider().chat_completion(
            [{"role": "user", "content": "safe"}],
            tools=[{"type": "function", "function": {"name": "probe"}}],
            tool_choice={"type": "function", "function": {"name": "probe"}},
        )
