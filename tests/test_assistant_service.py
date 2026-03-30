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

    def test_root_folder_context_is_preserved(self) -> None:
        assistant = self._assistant_with_ai(
            [
                '{"intent":"general","tool_calls":[],"final_answer":"Root summary.","proposed_actions":[],"needs_confirmation":false}',
            ]
        )

        response = assistant.handle_request(
            "What is in this folder?",
            AssistantContext(selected_root=str(self.root), open_folder_path="."),
            self.store.load(),
        )

        self.assertIn("open folder (approved root)", response.used_context)

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
