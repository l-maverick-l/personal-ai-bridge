from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class ProviderOption:
    key: str
    label: str
    description: str
    default_base_url: str
    local_only: bool


PROVIDER_OPTIONS: list[ProviderOption] = [
    ProviderOption(
        key="ollama",
        label="Ollama",
        description="Local Ollama server with no cloud API key required.",
        default_base_url="http://127.0.0.1:11434",
        local_only=True,
    ),
    ProviderOption(
        key="openai_local",
        label="OpenAI-compatible local endpoint",
        description="For LM Studio or another local OpenAI-compatible server.",
        default_base_url="http://127.0.0.1:1234/v1",
        local_only=True,
    ),
    ProviderOption(
        key="openai_cloud",
        label="OpenAI-compatible cloud endpoint",
        description="User-supplied hosted API endpoint and model.",
        default_base_url="https://api.openai.com/v1",
        local_only=False,
    ),
]


def get_provider_option(provider_key: str) -> ProviderOption | None:
    for option in PROVIDER_OPTIONS:
        if option.key == provider_key:
            return option
    return None
