from __future__ import annotations

import json

import pytest


def test_provider_payload_redacts_configured_secrets_and_common_pii(
    monkeypatch,
) -> None:
    monkeypatch.setenv("AIOPS_ENV", "test")
    monkeypatch.setenv("AIOPS_SESSION_SECRET", "session-secret-for-redaction")
    monkeypatch.setenv("AIOPS_LLM_MODE", "deepseek")
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
    from app.ai.providers import DeepSeekChatProvider

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
    DeepSeekChatProvider().chat_completion(messages)

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
    monkeypatch.setenv("AIOPS_LLM_MODE", "deepseek")
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
    from app.ai.providers import DeepSeekChatProvider, LLMProviderError

    with pytest.raises(LLMProviderError) as captured:
        DeepSeekChatProvider().chat_completion([{"role": "user", "content": "safe"}])
    message = str(captured.value)
    assert "supersecret" not in message
    assert "dXNlcjpwYXNzd29yZA==" not in message
    assert "[REDACTED_SECRET]" in message
