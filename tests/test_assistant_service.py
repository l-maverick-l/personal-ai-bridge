from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.assistant.manager import AssistantContext, AssistantIntent, AssistantService
from app.data.action_log import ActionLogger
from app.data.database import connect_database
from app.data.settings_store import SettingsStore
from app.email.yahoo_service import YahooMailService
from app.files.folder_registry import AllowedFolderRegistry
from app.files.service import FileService
from app.models.settings import AppSettings


class FakeAIClient:
    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
        self.calls: list[tuple[str, str]] = []

    def generate_text(self, settings, system_prompt: str, user_prompt: str, **kwargs) -> str:  # noqa: ANN001
        self.calls.append((system_prompt, user_prompt))
        if not self._responses:
            raise RuntimeError("No fake responses left")
        return self._responses.pop(0)

    def generate_structured_json(self, settings, system_prompt: str, user_prompt: str, **kwargs) -> str:  # noqa: ANN001
        return self.generate_text(settings, system_prompt, user_prompt, **kwargs)


class AssistantServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        connection = connect_database(self.temp_db.name)
        connection.close()
        self.registry = AllowedFolderRegistry(self.temp_db.name)
        self.logger = ActionLogger(self.temp_db.name)
        self.store = SettingsStore(self.temp_db.name)
        self.store.save(AppSettings())

        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "approved"
        self.root.mkdir()
        self.registry.add_folder(str(self.root))

    def tearDown(self) -> None:
        import os

        os.unlink(self.temp_db.name)
        self.tempdir.cleanup()

    def _assistant_with_ai(self, responses: list[str]) -> AssistantService:
        ai_client = FakeAIClient(responses)
        file_service = FileService(self.registry, self.logger, self.store, ai_client)
        yahoo_service = YahooMailService(self.store, ai_client, self.logger)
        return AssistantService(file_service, yahoo_service, ai_client)

    def test_agent_uses_tool_then_returns_final_answer(self) -> None:
        (self.root / "note.txt").write_text("hello", encoding="utf-8")
        assistant = self._assistant_with_ai(
            [
                '{"intent":"file","tool_calls":[{"name":"search_files","arguments":{"query":"note"}}],"final_answer":"","proposed_actions":[],"needs_confirmation":false}',
                '{"intent":"file","tool_calls":[],"final_answer":"I found note.txt.","proposed_actions":[],"needs_confirmation":false}',
            ]
        )

        response = assistant.handle_request(
            "Find files mentioning note",
            AssistantContext(selected_root=str(self.root)),
            self.store.load(),
        )

        self.assertEqual(response.intent, AssistantIntent.AGENT)
        self.assertEqual(response.answer_text, "I found note.txt.")
        self.assertEqual(response.proposed_actions, [])

    def test_delete_request_produces_confirmation_proposal(self) -> None:
        (self.root / "docs").mkdir(parents=True, exist_ok=True)
        (self.root / "docs" / "note.txt").write_text("hello", encoding="utf-8")
        assistant = self._assistant_with_ai(
            [
                '{"intent":"file","tool_calls":[{"name":"delete_file","arguments":{}}],"final_answer":"","proposed_actions":[],"needs_confirmation":true}',
                '{"intent":"file","tool_calls":[],"final_answer":"I prepared a delete action for confirmation.","proposed_actions":[],"needs_confirmation":true}',
            ]
        )

        response = assistant.handle_request(
            "Delete the selected file",
            AssistantContext(selected_root=str(self.root), selected_file_path="docs/note.txt"),
            self.store.load(),
        )

        self.assertEqual(len(response.proposed_actions), 1)
        self.assertEqual(response.proposed_actions[0].action_type, "file_delete")
        self.assertTrue((self.root / "docs" / "note.txt").exists())

    def test_invalid_model_output_triggers_repair_attempt(self) -> None:
        assistant = self._assistant_with_ai(
            [
                "not valid json",
                '{"intent":"general","tool_calls":[],"final_answer":"Done.","proposed_actions":[],"needs_confirmation":false}',
            ]
        )

        response = assistant.handle_request(
            "Just answer",
            AssistantContext(selected_root=str(self.root)),
            self.store.load(),
        )

        self.assertEqual(response.answer_text, "Done.")

    def test_missing_required_fields_returns_clear_error(self) -> None:
        assistant = self._assistant_with_ai(
            [
                '{"intent":"general","tool_calls":[],"final_answer":"Done."}',
                '{"intent":"general","tool_calls":[],"final_answer":"Done."}',
            ]
        )

        response = assistant.handle_request(
            "Just answer",
            AssistantContext(selected_root=str(self.root)),
            self.store.load(),
        )

        self.assertIn("missed required planner fields", response.answer_text)

    def test_root_folder_context_is_preserved(self) -> None:
        assistant = self._assistant_with_ai(
            [
                '{"intent":"file","tool_calls":[{"name":"list_directory","arguments":{"relative_path":"."}}],"final_answer":"","proposed_actions":[],"needs_confirmation":false}',
                '{"intent":"general","tool_calls":[],"final_answer":"Root summary.","proposed_actions":[],"needs_confirmation":false}',
            ]
        )

        response = assistant.handle_request(
            "What is in this folder?",
            AssistantContext(selected_root=str(self.root), open_folder_path="."),
            self.store.load(),
        )

        self.assertIn("open folder (approved root)", response.used_context)


    def test_send_email_always_requires_confirmation_even_on_full_auto(self) -> None:
        assistant = self._assistant_with_ai([])
        settings = self.store.load()
        settings.execution_policy = "full_auto"

        execution = assistant._execute_tool_call(
            {
                "name": "send_email",
                "arguments": {
                    "to_address": "friend@example.com",
                    "subject": "Hi",
                    "body": "Hello",
                },
            },
            AssistantContext(selected_root=str(self.root)),
            settings,
        )

        self.assertTrue(execution["ok"])
        self.assertIn("proposal", execution)
        self.assertEqual(execution["result"]["status"], "confirmation_required")
        self.assertNotIn("executed_action", execution)

    def test_list_inbox_accepts_start_and_end_dates(self) -> None:
        assistant = self._assistant_with_ai([])
        captured: dict[str, object] = {}

        def _fake_list_inbox(**kwargs):  # noqa: ANN001
            captured.update(kwargs)
            return []

        assistant._yahoo_service.list_inbox = _fake_list_inbox  # type: ignore[method-assign]
        settings = self.store.load()

        execution = assistant._execute_tool_call(
            {
                "name": "list_inbox",
                "arguments": {
                    "start_date": "2026-03-01",
                    "end_date": "2026-03-31",
                    "limit": 10,
                },
            },
            AssistantContext(selected_root=str(self.root)),
            settings,
        )

        self.assertTrue(execution["ok"])
        self.assertEqual(str(captured["start_date"]), "2026-03-01")
        self.assertEqual(str(captured["end_date"]), "2026-03-31")

    def test_list_inbox_invalid_date_returns_clear_error(self) -> None:
        assistant = self._assistant_with_ai([])

        execution = assistant._execute_tool_call(
            {
                "name": "list_inbox",
                "arguments": {
                    "start_date": "03/01/2026",
                },
            },
            AssistantContext(selected_root=str(self.root)),
            self.store.load(),
        )

        self.assertFalse(execution["ok"])
        self.assertIn("YYYY-MM-DD", execution["error"])

    def test_factual_request_rejects_ungrounded_final_answer(self) -> None:
        assistant = self._assistant_with_ai(
            [
                '{"intent":"email","tool_calls":[],"final_answer":"Your latest email says hello.","proposed_actions":[],"needs_confirmation":false}',
                '{"intent":"email","tool_calls":[],"final_answer":"Still ungrounded.","proposed_actions":[],"needs_confirmation":false}',
            ]
        )

        response = assistant.handle_request(
            "What does my latest email say?",
            AssistantContext(selected_root=str(self.root)),
            self.store.load(),
        )

        self.assertIn("could not provide a grounded factual answer", response.answer_text)

    def test_status_updates_are_emitted_during_agent_flow(self) -> None:
        (self.root / "note.txt").write_text("hello", encoding="utf-8")
        assistant = self._assistant_with_ai(
            [
                '{"intent":"file","tool_calls":[{"name":"search_files","arguments":{"query":"note"}}],"final_answer":"","proposed_actions":[],"needs_confirmation":false}',
                '{"intent":"file","tool_calls":[],"final_answer":"I found note.txt.","proposed_actions":[],"needs_confirmation":false}',
            ]
        )
        statuses: list[str] = []

        assistant.handle_request(
            "Find files mentioning note",
            AssistantContext(selected_root=str(self.root)),
            self.store.load(),
            on_status=statuses.append,
        )

        self.assertIn("analyzing request", statuses)
        self.assertIn("planning tools", statuses)
        self.assertIn("running tool call", statuses)
        self.assertIn("generating final answer", statuses)


if __name__ == "__main__":
    unittest.main()
