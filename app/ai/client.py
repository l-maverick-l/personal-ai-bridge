from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request

from app.models.settings import AppSettings


class AIClientError(RuntimeError):
    """Raised when the configured AI provider cannot satisfy a request."""


class AIUnavailableError(AIClientError):
    """Raised when AI is not configured or not reachable."""


@dataclass(slots=True)
class AIClient:
    timeout_seconds: int = 30

    def is_available(self, settings: AppSettings) -> bool:
        provider = settings.provider
        return (
            settings.ai_mode != "skip"
            and provider.provider_type != "none"
            and bool(provider.base_url.strip())
            and bool(provider.model_name.strip())
        )

    def summarize_text(self, text: str, settings: AppSettings) -> str:
        if not self.is_available(settings):
            raise AIUnavailableError(
                "AI summarization is not available because no provider and model are configured."
            )
        provider = settings.provider
        prompt = (
            "Summarize the following file for a non-technical user. "
            "Use short bullets and keep important names, dates, and action items.\n\n"
            f"File content:\n{text[:12000]}"
        )
        if provider.provider_type == "ollama":
            return self._call_ollama(provider.base_url, provider.model_name, prompt)
        if provider.provider_type in {"openai_local", "openai_cloud"}:
            return self._call_openai_compatible(
                provider.base_url,
                provider.model_name,
                provider.api_key,
                prompt,
            )
        raise AIUnavailableError(f"Unsupported AI provider type: {provider.provider_type}")

    def _call_ollama(self, base_url: str, model_name: str, prompt: str) -> str:
        payload = {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
        }
        response = self._post_json(f"{base_url.rstrip('/')}/api/generate", payload)
        summary = response.get("response", "").strip()
        if not summary:
            raise AIClientError("The AI provider returned an empty summary.")
        return summary

    def _call_openai_compatible(
        self,
        base_url: str,
        model_name: str,
        api_key: str,
        prompt: str,
    ) -> str:
        payload = {
            "model": model_name,
            "messages": [
                {
                    "role": "system",
                    "content": "You summarize user files clearly and briefly.",
                },
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.2,
        }
        headers: dict[str, str] = {}
        if api_key.strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"
        response = self._post_json(
            f"{base_url.rstrip('/')}/chat/completions",
            payload,
            headers=headers,
        )
        choices = response.get("choices") or []
        if not choices:
            raise AIClientError("The AI provider returned no choices.")
        message = choices[0].get("message") or {}
        summary = str(message.get("content", "")).strip()
        if not summary:
            raise AIClientError("The AI provider returned an empty summary.")
        return summary

    def _post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = {
            "Content-Type": "application/json",
            **(headers or {}),
        }
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(url, data=body, headers=request_headers, method="POST")
        try:
            with request.urlopen(http_request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise AIClientError(f"AI provider HTTP error {exc.code}: {details}") from exc
        except error.URLError as exc:
            raise AIUnavailableError(f"Could not reach the AI provider: {exc.reason}") from exc
