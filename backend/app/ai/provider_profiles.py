from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True)
class LLMProviderProfile:
    provider_id: str
    display_name: str
    production_base_url: str | None
    candidate_model: str | None
    api_key_variable: str | None = None
    models_path: str | None = "/models"
    chat_path: str = "/chat/completions"
    supports_streaming: bool = False
    supports_tool_calls: bool = False
    reports_usage: bool = False
    supports_named_tool_choice: bool = False
    supports_stream_usage_option: bool = False
    cache_usage_format: str = "prompt_tokens_details"
    supports_thinking: bool = False
    supports_reasoning_effort: bool = False

    @property
    def discovery_method(self) -> str:
        return (
            "authenticated_models_endpoint"
            if self.models_path
            else "authenticated_chat_completion"
        )

    def validate_base_url(self, base_url: str, *, production: bool) -> None:
        parsed = urlparse(base_url)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "LLM base URL must be a credential-free HTTPS URL without query or fragment"
            )
        if production and self.production_base_url:
            if base_url.rstrip("/") != self.production_base_url:
                raise ValueError(
                    f"{self.display_name} production base URL must be {self.production_base_url}"
                )


PROVIDER_PROFILES: dict[str, LLMProviderProfile] = {
    "deepseek": LLMProviderProfile(
        provider_id="deepseek",
        display_name="DeepSeek",
        production_base_url="https://api.deepseek.com",
        candidate_model="deepseek-v4-flash",
        api_key_variable="AIOPS_DEEPSEEK_API_KEY",
        supports_streaming=True,
        supports_tool_calls=True,
        reports_usage=True,
        supports_named_tool_choice=True,
        supports_stream_usage_option=True,
        cache_usage_format="deepseek_cache_fields",
        supports_thinking=True,
        supports_reasoning_effort=True,
    ),
    "moonshot": LLMProviderProfile(
        provider_id="moonshot",
        display_name="Moonshot Kimi",
        production_base_url="https://api.moonshot.ai/v1",
        candidate_model="kimi-k2.6",
        api_key_variable="AIOPS_MOONSHOT_API_KEY",
        supports_streaming=True,
        supports_tool_calls=True,
        reports_usage=True,
        supports_stream_usage_option=True,
        cache_usage_format="kimi_cached_tokens",
        supports_thinking=True,
    ),
    "zhipu": LLMProviderProfile(
        provider_id="zhipu",
        display_name="Zhipu GLM",
        production_base_url="https://open.bigmodel.cn/api/paas/v4",
        candidate_model="glm-5.2",
        api_key_variable="AIOPS_ZHIPU_API_KEY",
        models_path=None,
        supports_streaming=True,
        supports_tool_calls=True,
        reports_usage=True,
        supports_thinking=True,
        supports_reasoning_effort=True,
    ),
    "openai_compatible": LLMProviderProfile(
        provider_id="openai_compatible",
        display_name="Custom OpenAI-compatible provider",
        production_base_url=None,
        candidate_model=None,
        supports_streaming=True,
        supports_tool_calls=True,
        reports_usage=True,
        supports_named_tool_choice=True,
        supports_stream_usage_option=True,
    ),
}

LIVE_PROVIDER_IDS = frozenset(PROVIDER_PROFILES)


def get_provider_profile(provider_id: str) -> LLMProviderProfile:
    try:
        return PROVIDER_PROFILES[provider_id]
    except KeyError as exc:
        supported = ", ".join(sorted(PROVIDER_PROFILES))
        raise ValueError(
            f"unsupported LLM provider {provider_id!r}; expected one of: {supported}"
        ) from exc
