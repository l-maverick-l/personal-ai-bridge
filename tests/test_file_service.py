from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.ai.client import AIClient
from app.data.action_log import ActionLogger
from app.data.database import initialize_database
from app.data.settings_store import SettingsStore
from app.files.folder_registry import AllowedFolderRegistry
from app.files.service import FileOperationError, FileService
from app.models.settings import AppSettings
from app.security.path_guard import PathAccessError, PathGuard


class FileServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        initialize_database(self.connection)
        self.registry = AllowedFolderRegistry(self.connection)
        self.logger = ActionLogger(self.connection)
        self.settings_store = SettingsStore(self.connection)
        self.settings_store.save(AppSettings())
        self.service = FileService(
            folder_registry=self.registry,
            action_logger=self.logger,
            settings_store=self.settings_store,
            ai_client=AIClient(),
        )
        self.tempdir = tempfile.TemporaryDirectory()
        self.root = Path(self.tempdir.name) / "approved"
        self.root.mkdir()
        self.registry.add_folder(str(self.root))

    def tearDown(self) -> None:
        self.connection.close()
        self.tempdir.cleanup()

    def test_resolve_relative_path_blocks_traversal(self) -> None:
        with self.assertRaises(PathAccessError):
            PathGuard.resolve_relative_path(self.root, "../escape.txt")

    def test_list_search_read_create_and_rename_file(self) -> None:
        created = Path(self.service.create_file(str(self.root), "notes/example.txt", "hello world"))
        self.assertTrue(created.exists())

        listing = self.service.list_directory(str(self.root), "notes")
        self.assertEqual([entry.relative_path for entry in listing.entries], ["notes/example.txt"])

        matches = self.service.search_files(str(self.root), "example")
        self.assertEqual([entry.relative_path for entry in matches], ["notes/example.txt"])

        read_result = self.service.read_file(str(self.root), "notes/example.txt")
        self.assertEqual(read_result.content, "hello world")

        renamed = Path(self.service.rename_file(str(self.root), "notes/example.txt", "renamed.txt"))
        self.assertEqual(renamed.name, "renamed.txt")
        self.assertTrue(renamed.exists())

    def test_copy_move_and_delete_file(self) -> None:
        source = Path(self.service.create_file(str(self.root), "source.txt", "copy me"))
        copied = Path(
            self.service.copy_file(
                str(self.root),
                "source.txt",
                str(self.root),
                "copy.txt",
            )
        )
        self.assertTrue(copied.exists())

        moved = Path(
            self.service.move_file(
                str(self.root),
                "copy.txt",
                str(self.root),
                "nested/moved.txt",
            )
        )
        self.assertTrue(moved.exists())
        self.assertFalse(copied.exists())

        trash_path = Path(self.service.delete_file(str(self.root), "nested/moved.txt"))
        self.assertTrue(trash_path.exists())
        self.assertFalse(moved.exists())
        self.assertTrue(source.exists())

    def test_summary_requires_ai_configuration_but_other_actions_still_work(self) -> None:
        self.service.create_file(str(self.root), "summary.txt", "This should still be readable.")
        read_result = self.service.read_file(str(self.root), "summary.txt")
        self.assertIn("readable", read_result.content)
        with self.assertRaises(FileOperationError):
            self.service.summarize_file(str(self.root), "summary.txt")

    def test_create_rejects_docx_and_pdf_generation(self) -> None:
        with self.assertRaises(FileOperationError):
            self.service.create_file(str(self.root), "blank.docx", "")
        with self.assertRaises(FileOperationError):
            self.service.create_file(str(self.root), "blank.pdf", "")


if __name__ == "__main__":
    unittest.main()
