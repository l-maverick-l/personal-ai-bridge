from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.ai.client import AIClient
from app.assistant.manager import AssistantContext, AssistantIntent, AssistantService
from app.data.action_log import ActionLogger
from app.data.database import initialize_database
from app.data.settings_store import SettingsStore
from app.email.yahoo_service import YahooMailService
from app.files.folder_registry import AllowedFolderRegistry
from app.files.service import FileService
from app.models.settings import AppSettings


class AssistantServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        initialize_database(self.connection)
        self.registry = AllowedFolderRegistry(self.connection)
        self.logger = ActionLogger(self.connection)
        self.store = SettingsStore(self.connection)
        self.store.save(AppSettings())
        self.ai_client = AIClient()
        self.file_service = FileService(self.registry, self.logger, self.store, self.ai_client)
        self.yahoo_service = YahooMailService(self.store, self.ai_client, self.logger)
        self.assistant = AssistantService(self.file_service, self.yahoo_service, self.ai_client)

        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "approved"
        self.root.mkdir()
        self.registry.add_folder(str(self.root))
        self.file_service.create_file(str(self.root), "docs/note.txt", "hello")

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def test_classify_intents(self) -> None:
        self.assertEqual(self.assistant.classify_intent("What files mention invoice?"), AssistantIntent.FILE_READ_SEARCH)
        self.assertEqual(self.assistant.classify_intent("Move this file to Taxes/2026"), AssistantIntent.FILE_ACTION)
        self.assertEqual(self.assistant.classify_intent("Draft a reply saying thanks"), AssistantIntent.EMAIL_DRAFT_SEND)

    def test_delete_action_is_proposal_only(self) -> None:
        context = AssistantContext(selected_root=str(self.root), selected_file_path="docs/note.txt")
        response = self.assistant.handle_request("Delete the selected file", context, self.store.load())
        self.assertEqual(response.intent, AssistantIntent.FILE_ACTION)
        self.assertEqual(len(response.proposed_actions), 1)
        self.assertEqual(response.proposed_actions[0].action_type, "file_delete")
        self.assertTrue((self.root / "docs/note.txt").exists())

    def test_search_files_works_without_ai(self) -> None:
        context = AssistantContext(selected_root=str(self.root))
        response = self.assistant.handle_request("What files mention note", context, self.store.load())
        self.assertIn("note.txt", response.answer_text)


if __name__ == "__main__":
    unittest.main()
