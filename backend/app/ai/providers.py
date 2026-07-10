from __future__ import annotations

import os
from time import sleep
from typing import Any

import httpx

from app.config import Settings, load_settings
from app.ai.provider_profiles import (
    LIVE_PROVIDER_IDS,
    LLMProviderProfile,
    get_provider_profile,
)
from app.ai.redaction import configured_secrets, redact_for_provider, redact_text


class LLMProviderError(RuntimeError):
    pass


def _provider_settings(settings: Settings | None = None) -> Settings:
    """Use the canonical config loader for all provider credentials and routing."""
    return settings or load_settings()


def llm_mode(settings: Settings | None = None) -> str:
    return _provider_settings(settings).llm_mode


def llm_enabled(settings: Settings | None = None) -> bool:
    settings = _provider_settings(settings)
    return settings.llm_mode in settings.llm_enabled_providers and bool(
        settings.llm_api_key and settings.llm_base_url and settings.llm_model
    )


def deepseek_enabled(settings: Settings | None = None) -> bool:
    """Backward-compatible alias; callers should use llm_enabled."""
    return llm_enabled(settings)


def model_name(settings: Settings | None = None) -> str:
    settings = _provider_settings(settings)
    if settings.llm_mode in LIVE_PROVIDER_IDS:
        return settings.llm_model or "unconfigured"
    return settings.llm_mode


class OpenAICompatibleChatProvider:
    def __init__(self, settings: Settings | None = None) -> None:
        settings = _provider_settings(settings)
        self.settings = settings
        try:
            self.profile: LLMProviderProfile = get_provider_profile(settings.llm_mode)
        except ValueError as exc:
            raise LLMProviderError(str(exc)) from exc
        self.api_key = settings.llm_api_key
        if not self.api_key:
            raise LLMProviderError(f"{self.profile.display_name} API key is not configured")
        if not settings.llm_base_url or not settings.llm_model:
            raise LLMProviderError("LLM base URL and model must be configured explicitly")
        self.base_url = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model
        self.timeout = float(os.getenv("AIOPS_LLM_TIMEOUT_SECONDS", "60"))
        self.thinking = os.getenv(
            "AIOPS_LLM_THINKING",
            os.getenv("AIOPS_DEEPSEEK_THINKING", "enabled"),
        ).strip().lower()
        self.reasoning_effort = os.getenv(
            "AIOPS_LLM_REASONING_EFFORT",
            os.getenv("AIOPS_DEEPSEEK_REASONING_EFFORT", "high"),
        )
        self.max_tokens = int(os.getenv("AIOPS_LLM_MAX_TOKENS", "1800"))
        self.retries = int(os.getenv("AIOPS_LLM_RETRIES", "2"))

    def _safe_provider_detail(self, value: str) -> str:
        return redact_text(value[:500], secrets=configured_secrets(self.settings))

    def chat_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str | dict[str, Any] | None = "auto",
        response_format: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": redact_for_provider(messages, settings=self.settings),
            "stream": False,
            "max_tokens": self.max_tokens,
        }
        if self.profile.supports_thinking and self.thinking in {"enabled", "disabled"}:
            payload["thinking"] = {"type": self.thinking}
            if self.thinking == "enabled" and self.profile.supports_reasoning_effort:
                payload["reasoning_effort"] = self.reasoning_effort
        if tools:
            if not self.profile.supports_tool_calls:
                raise LLMProviderError(
                    f"{self.profile.display_name} tool calling is not enabled by its provider profile"
                )
            if isinstance(tool_choice, dict) and not self.profile.supports_named_tool_choice:
                raise LLMProviderError(
                    f"{self.profile.display_name} does not support named tool choice"
                )
            payload["tools"] = tools
            payload["tool_choice"] = tool_choice or "auto"
        elif tool_choice:
            payload["tool_choice"] = tool_choice
        if response_format:
            payload["response_format"] = response_format

        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                response = httpx.post(
                    f"{self.base_url}{self.profile.chat_path}",
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code < 500:
                    if response.status_code >= 400:
                        detail = self._safe_provider_detail(response.text)
                        raise LLMProviderError(
                            f"{self.profile.display_name} returned {response.status_code}: {detail}"
                        )
                    return response.json()
                last_error = LLMProviderError(
                    f"{self.profile.display_name} returned {response.status_code}: "
                    f"{self._safe_provider_detail(response.text)}"
                )
            except httpx.HTTPError as exc:
                last_error = exc
            if attempt < self.retries:
                sleep(0.8 * (attempt + 1))
        raise LLMProviderError(f"{self.profile.display_name} request failed: {last_error}")


# Retain the import surface used by older deployments while the implementation
# is provider-neutral.
DeepSeekChatProvider = OpenAICompatibleChatProvider
