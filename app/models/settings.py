from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(slots=True)
class ProviderConfig:
    provider_type: str = "none"
    label: str = "Not configured"
    base_url: str = ""
    model_name: str = ""
    api_key: str = ""
    local_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ProviderConfig":
        payload = payload or {}
        return cls(
            provider_type=payload.get("provider_type", "none"),
            label=payload.get("label", "Not configured"),
            base_url=payload.get("base_url", ""),
            model_name=payload.get("model_name", ""),
            api_key=payload.get("api_key", ""),
            local_only=bool(payload.get("local_only", True)),
        )


@dataclass(slots=True)
class AppSettings:
    ai_mode: str = "skip"
    provider: ProviderConfig = field(default_factory=ProviderConfig)
    yahoo_email: str = ""
    yahoo_app_password: str = ""
    setup_complete: bool = False

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["provider"] = self.provider.to_dict()
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "AppSettings":
        payload = payload or {}
        return cls(
            ai_mode=payload.get("ai_mode", "skip"),
            provider=ProviderConfig.from_dict(payload.get("provider")),
            yahoo_email=payload.get("yahoo_email", ""),
            yahoo_app_password=payload.get("yahoo_app_password", ""),
            setup_complete=bool(payload.get("setup_complete", False)),
        )
