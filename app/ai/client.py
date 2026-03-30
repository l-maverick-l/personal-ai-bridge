from __future__ import annotations

import json
import re
import socket
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib import error, request

from app.models.settings import AppSettings


class AIClientError(RuntimeError):
    """Raised when the configured AI provider cannot satisfy a request."""


class AIUnavailableError(AIClientError):
    """Raised when AI is not configured or not reachable."""


class AITimeoutError(AIClientError):
    """Raised when the provider did not respond in time."""


class AIModelOutputError(AIClientError):
    """Raised when the provider response is syntactically present but unusable."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


@dataclass(slots=True)
class AIProviderTestResult:
    provider: str
    model: str
    success: bool
    elapsed_seconds: float
    message: str


@dataclass(slots=True)
class AIClient:
    default_local_timeout_seconds: int = 180
    default_cloud_timeout_seconds: int = 45

    def is_available(self, settings: AppSettings) -> bool:
        provider = settings.provider
        return (
            settings.ai_mode != "skip"
            and provider.provider_type != "none"
            and bool(provider.base_url.strip())
            and bool(provider.model_name.strip())
        )

    def summarize_text(
        self,
        text: str,
        settings: AppSettings,
        on_status: Callable[[str], None] | None = None,
        on_partial: Callable[[str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> str:
        compact_text = self._compact_for_local_model(text, settings)
        prompt = (
            "Summarize the following file for a non-technical user. "
            "Use short bullets and keep important names, dates, and action items.\n\n"
            f"File content:\n{compact_text}"
        )
        return self.generate_text(
            settings,
            system_prompt="You summarize user files clearly and briefly.",
            user_prompt=prompt,
            on_status=on_status,
            on_partial=on_partial,
            is_cancelled=is_cancelled,
        )

    def generate_text(
        self,
        settings: AppSettings,
        system_prompt: str,
        user_prompt: str,
        on_status: Callable[[str], None] | None = None,
        on_partial: Callable[[str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> str:
        if not self.is_available(settings):
            raise AIUnavailableError(
                "AI features are not available because no provider and model are configured."
            )
        provider = settings.provider
        request_timeout = self._timeout_for_settings(settings)
        if provider.provider_type == "ollama":
            return self._call_ollama(
                provider.base_url,
                provider.model_name,
                system_prompt,
                user_prompt,
                request_timeout,
                on_status=on_status,
                on_partial=on_partial,
                is_cancelled=is_cancelled,
            )
        if provider.provider_type in {"openai_local", "openai_cloud"}:
            return self._call_openai_compatible(
                provider.base_url,
                provider.model_name,
                provider.api_key,
                system_prompt,
                user_prompt,
                request_timeout,
                on_status=on_status,
            )
        raise AIUnavailableError(f"Unsupported AI provider type: {provider.provider_type}")

    def test_provider(self, settings: AppSettings) -> AIProviderTestResult:
        start = time.monotonic()
        provider = settings.provider
        try:
            self.generate_text(
                settings=settings,
                system_prompt="You are a concise assistant.",
                user_prompt="Reply with exactly: OK",
            )
            elapsed = time.monotonic() - start
            return AIProviderTestResult(
                provider=provider.label,
                model=provider.model_name,
                success=True,
                elapsed_seconds=elapsed,
                message="Provider test succeeded.",
            )
        except AIClientError as exc:
            elapsed = time.monotonic() - start
            return AIProviderTestResult(
                provider=provider.label,
                model=provider.model_name,
                success=False,
                elapsed_seconds=elapsed,
                message=str(exc),
            )

    def _call_ollama(
        self,
        base_url: str,
        model_name: str,
        system_prompt: str,
        user_prompt: str,
        request_timeout: int,
        on_status: Callable[[str], None] | None = None,
        on_partial: Callable[[str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        json_mode: bool = False,
        json_schema: dict[str, Any] | None = None,
        disable_thinking: bool = False,
    ) -> str:
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": True,
            "options": {"temperature": 0.2},
        }
        if json_mode:
            payload["format"] = json_schema or "json"
            payload["options"] = {"temperature": 0}
            if disable_thinking:
                payload["think"] = False
        if on_status:
            on_status("Connecting to AI provider")
        try:
            response_chunks = self._stream_json_lines(
                f"{base_url.rstrip('/')}/api/chat",
                payload,
                timeout_seconds=request_timeout,
                on_status=on_status,
                is_cancelled=is_cancelled,
            )
        except AIClientError as exc:
            if not self._should_retry_without_thinking(disable_thinking=disable_thinking, error=exc):
                raise
            payload.pop("think", None)
            response_chunks = self._stream_json_lines(
                f"{base_url.rstrip('/')}/api/chat",
                payload,
                timeout_seconds=request_timeout,
                on_status=on_status,
                is_cancelled=is_cancelled,
            )
        chunks: list[str] = []
        saw_reasoning_chunk = False
        saw_response_events = False
        for item in response_chunks:
            piece, saw_reasoning = self._extract_ollama_chunk_output(item)
            saw_reasoning_chunk = saw_reasoning_chunk or saw_reasoning
            saw_response_events = True
            if piece:
                chunks.append(piece)
                if on_partial:
                    on_partial("".join(chunks))
        summary = "".join(chunks).strip()
        if not summary:
            if saw_response_events and saw_reasoning_chunk:
                raise AIModelOutputError(
                    reason="reasoning_only_stream",
                    message=(
                        "The local model streamed reasoning/thinking events but never emitted a final answer."
                    ),
                )
            if saw_response_events:
                raise AIModelOutputError(
                    reason="no_final_answer",
                    message="The local model returned events but never emitted a usable final answer.",
                )
            raise AIModelOutputError(
                reason="no_stream",
                message="The local model returned no stream events.",
            )
        return summary

    def generate_structured_json(
        self,
        settings: AppSettings,
        system_prompt: str,
        user_prompt: str,
        on_status: Callable[[str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> str:
        provider = settings.provider
        request_timeout = self._timeout_for_settings(settings)
        if provider.provider_type == "ollama":
            planner_schema = {
                "type": "object",
                "properties": {
                    "intent": {"type": "string"},
                    "tool_calls": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "arguments": {"type": "object"},
                            },
                            "required": ["name", "arguments"],
                        },
                    },
                    "final_answer": {"type": "string"},
                    "proposed_actions": {"type": "array"},
                    "needs_confirmation": {"type": "boolean"},
                },
                "required": [
                    "intent",
                    "tool_calls",
                    "final_answer",
                    "proposed_actions",
                    "needs_confirmation",
                ],
            }
            return self._call_ollama(
                provider.base_url,
                provider.model_name,
                system_prompt,
                user_prompt,
                request_timeout,
                on_status=on_status,
                is_cancelled=is_cancelled,
                json_mode=True,
                json_schema=planner_schema,
                disable_thinking=True,
            )
        return self.generate_text(
            settings=settings,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            on_status=on_status,
            is_cancelled=is_cancelled,
        )

    def _call_openai_compatible(
        self,
        base_url: str,
        model_name: str,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
        request_timeout: int,
        on_status: Callable[[str], None] | None = None,
    ) -> str:
        payload = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
        }
        headers: dict[str, str] = {}
        if api_key.strip():
            headers["Authorization"] = f"Bearer {api_key.strip()}"
        if on_status:
            on_status("Generating response")
        response = self._post_json(
            f"{base_url.rstrip('/')}/chat/completions",
            payload,
            timeout_seconds=request_timeout,
            headers=headers,
        )
        choices = response.get("choices") or []
        if not choices:
            raise AIClientError("The AI provider returned no choices.")
        message = choices[0].get("message") or {}
        summary = str(message.get("content", "")).strip()
        if not summary:
            raise AIClientError("The AI provider returned an empty response.")
        return summary

    def _post_json(
        self,
        url: str,
        payload: dict[str, Any],
        timeout_seconds: int,
        headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        request_headers = {
            "Content-Type": "application/json",
            **(headers or {}),
        }
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(url, data=body, headers=request_headers, method="POST")
        try:
            with request.urlopen(http_request, timeout=timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise AITimeoutError(
                "The AI provider timed out before completing the request."
            ) from exc
        except socket.timeout as exc:
            raise AITimeoutError(
                "The AI provider timed out before completing the request."
            ) from exc
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            lowered = details.lower()
            if exc.code == 404 or "model" in lowered and "not found" in lowered:
                raise AIClientError(
                    "The configured model was not found on the AI provider. Check the model name."
                ) from exc
            raise AIClientError(f"AI provider HTTP error {exc.code}: {details}") from exc
        except error.URLError as exc:
            raise AIUnavailableError(f"Could not reach the AI provider: {exc.reason}") from exc

    def _stream_json_lines(
        self,
        url: str,
        payload: dict[str, Any],
        timeout_seconds: int,
        on_status: Callable[[str], None] | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> list[dict[str, Any]]:
        request_headers = {"Content-Type": "application/json"}
        body = json.dumps(payload).encode("utf-8")
        http_request = request.Request(url, data=body, headers=request_headers, method="POST")
        parsed: list[dict[str, Any]] = []
        saw_tokens = False
        try:
            with request.urlopen(http_request, timeout=timeout_seconds) as response:
                if on_status:
                    on_status("Waiting for model")
                for raw_line in response:
                    if is_cancelled and is_cancelled():
                        raise AIClientError("Request cancelled.")
                    line = raw_line.decode("utf-8", errors="replace").strip()
                    if not line:
                        continue
                    chunk = json.loads(line)
                    parsed.append(chunk)
                    if chunk.get("response"):
                        if not saw_tokens and on_status:
                            on_status("Generating response")
                        saw_tokens = True
        except TimeoutError as exc:
            if saw_tokens:
                raise AITimeoutError(
                    "The local model was still generating, but no new output arrived before timeout."
                ) from exc
            raise AITimeoutError(
                "The AI provider did not respond before timeout. Local models may need a higher timeout."
            ) from exc
        except socket.timeout as exc:
            if saw_tokens:
                raise AITimeoutError(
                    "The local model was still generating, but no new output arrived before timeout."
                ) from exc
            raise AITimeoutError(
                "The AI provider did not respond before timeout. Local models may need a higher timeout."
            ) from exc
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            lowered = details.lower()
            if exc.code == 404 or "model" in lowered and "not found" in lowered:
                raise AIClientError(
                    "The configured model was not found on the AI provider. Check the model name."
                ) from exc
            raise AIClientError(f"AI provider HTTP error {exc.code}: {details}") from exc
        except error.URLError as exc:
            raise AIUnavailableError(f"Could not reach the AI provider: {exc.reason}") from exc
        return parsed

    def _timeout_for_settings(self, settings: AppSettings) -> int:
        provider_type = settings.provider.provider_type
        if provider_type in {"ollama", "openai_local"}:
            return max(10, int(settings.ai_local_timeout_seconds or self.default_local_timeout_seconds))
        return max(10, int(settings.ai_cloud_timeout_seconds or self.default_cloud_timeout_seconds))

    def _compact_for_local_model(self, text: str, settings: AppSettings) -> str:
        max_chars = 12000
        if settings.provider.provider_type in {"ollama", "openai_local"}:
            max_chars = 8000
        cleaned = text.strip()
        if len(cleaned) <= max_chars:
            return cleaned
        head = cleaned[: max_chars // 2]
        tail = cleaned[-(max_chars // 2) :]
        return f"{head}\n\n[...content trimmed for length...]\n\n{tail}"

    def _extract_ollama_chunk_output(self, chunk: dict[str, Any]) -> tuple[str, bool]:
        reasoning_fields = ["thinking", "reasoning", "thought"]
        message = chunk.get("message")
        output_parts: list[str] = []
        saw_reasoning = False
        if isinstance(message, dict):
            message_content = str(message.get("content") or "")
            if message_content:
                output_parts.append(message_content)
            for field in reasoning_fields:
                if str(message.get(field) or "").strip():
                    saw_reasoning = True
        response_content = str(chunk.get("response") or "")
        if response_content:
            output_parts.append(response_content)
        for field in reasoning_fields:
            if str(chunk.get(field) or "").strip():
                saw_reasoning = True
        return "".join(output_parts), saw_reasoning

    def _should_retry_without_thinking(self, disable_thinking: bool, error: Exception) -> bool:
        if not disable_thinking:
            return False
        message = str(error).lower()
        if "http error 400" not in message:
            return False
        return bool(re.search(r"(unknown|invalid).*(think)", message))
