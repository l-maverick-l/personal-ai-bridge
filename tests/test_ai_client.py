from __future__ import annotations

import unittest

from app.ai.client import AIClient, AIClientError, AIModelOutputError
from app.assistant.manager import AssistantService
from app.models.settings import AppSettings, ProviderConfig


class StubAIClient(AIClient):
    def __init__(self, chunks_by_call: list[list[dict]] | None = None, errors_by_call: list[Exception] | None = None) -> None:
        super().__init__()
        self.chunks_by_call = chunks_by_call or []
        self.errors_by_call = errors_by_call or []
        self.payloads: list[dict] = []

    def _stream_json_lines(self, url, payload, timeout_seconds, on_status=None, is_cancelled=None):  # noqa: ANN001
        self.payloads.append(dict(payload))
        call_index = len(self.payloads) - 1
        if call_index < len(self.errors_by_call) and self.errors_by_call[call_index] is not None:
            raise self.errors_by_call[call_index]
        if call_index < len(self.chunks_by_call):
            return self.chunks_by_call[call_index]
        return []


class AIClientOllamaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = AppSettings(
            ai_mode="local",
            provider=ProviderConfig(
                provider_type="ollama",
                label="Ollama",
                base_url="http://localhost:11434",
                model_name="tiny",
            ),
        )

    def test_reasoning_only_stream_raises_specific_error(self) -> None:
        client = StubAIClient(chunks_by_call=[[{"message": {"thinking": "I should call tools"}}, {"done": True}]])
        with self.assertRaises(AIModelOutputError) as ctx:
            client.generate_structured_json(self.settings, "sys", "usr")
        self.assertEqual(ctx.exception.reason, "reasoning_only_stream")

    def test_no_stream_raises_specific_error(self) -> None:
        client = StubAIClient(chunks_by_call=[[]])
        with self.assertRaises(AIModelOutputError) as ctx:
            client.generate_structured_json(self.settings, "sys", "usr")
        self.assertEqual(ctx.exception.reason, "no_stream")

    def test_response_field_tokens_are_collected(self) -> None:
        client = StubAIClient(
            chunks_by_call=[[
                {"response": '{"intent":"general",'},
                {"response": '"tool_calls":[],"final_answer":"Done.","proposed_actions":[],"needs_confirmation":false}'},
            ]]
        )
        output = client.generate_structured_json(self.settings, "sys", "usr")
        self.assertIn('"final_answer":"Done."', output)

    def test_retry_without_think_on_unsupported_option(self) -> None:
        client = StubAIClient(
            chunks_by_call=[[], [{"message": {"content": '{"intent":"general","tool_calls":[],"final_answer":"Done.","proposed_actions":[],"needs_confirmation":false}'}}]],
            errors_by_call=[AIClientError('AI provider HTTP error 400: unknown field "think"')],
        )
        output = client.generate_structured_json(self.settings, "sys", "usr")
        self.assertIn('"intent":"general"', output)
        self.assertEqual(client.payloads[0].get("think"), False)
        self.assertNotIn("think", client.payloads[1])

    def test_generate_text_retries_when_first_attempt_is_reasoning_only(self) -> None:
        client = StubAIClient(
            chunks_by_call=[
                [{"message": {"thinking": "analyzing"}}, {"done": True}],
                [{"message": {"content": "Final draft body"}}],
            ]
        )
        output = client.generate_text(self.settings, "sys", "usr")
        self.assertEqual(output, "Final draft body")
        self.assertEqual(client.payloads[0].get("think"), False)

    def test_generate_text_raises_empty_output_reason(self) -> None:
        client = StubAIClient(chunks_by_call=[[{"message": {"content": "   "}}, {"done": True}]])
        with self.assertRaises(AIModelOutputError) as ctx:
            client.generate_text(self.settings, "sys", "usr")
        self.assertEqual(ctx.exception.reason, "empty_output")

    def test_generate_text_streams_progressive_partial_updates(self) -> None:
        client = StubAIClient(
            chunks_by_call=[[
                {"message": {"content": "Hello"}},
                {"message": {"content": " world"}},
            ]]
        )
        partials: list[str] = []
        output = client.generate_text(self.settings, "sys", "usr", on_partial=partials.append)
        self.assertEqual(output, "Hello world")
        self.assertEqual(partials, ["Hello", "Hello world"])

    def test_assistant_maps_reasoning_only_error(self) -> None:
        message = AssistantService._planner_error_from_exception(  # type: ignore[attr-defined]
            AssistantService,
            AIModelOutputError(
                reason="reasoning_only_stream",
                message="reasoning only",
            ),
        )
        self.assertIn("reasoning/thinking output only", message)


if __name__ == "__main__":
    unittest.main()
