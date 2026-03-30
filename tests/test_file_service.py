from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.ai.client import AIClient
from app.data.action_log import ActionLogger
from app.data.database import connect_database
from app.data.settings_store import SettingsStore
from app.files.folder_registry import AllowedFolderRegistry
from app.files.service import FileOperationError, FileService
from app.models.settings import AppSettings
from app.security.path_guard import PathAccessError, PathGuard


class FileServiceTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        connection = connect_database(self.temp_db.name)
        connection.close()
        self.registry = AllowedFolderRegistry(self.temp_db.name)
        self.logger = ActionLogger(self.temp_db.name)
        self.settings_store = SettingsStore(self.temp_db.name)
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
        import os

        os.unlink(self.temp_db.name)
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

    def test_read_doc_uses_fallback_extractors(self) -> None:
        doc_file = self.root / "legacy.doc"
        doc_file.write_bytes(b"legacy")

        with (
            patch.object(self.service, "_read_doc_with_word_com", side_effect=FileOperationError("word missing")),
            patch.object(self.service, "_read_doc_with_libreoffice", return_value="Converted via libreoffice"),
            patch.object(self.service, "_read_doc_with_antiword") as antiword_mock,
        ):
            result = self.service.read_file(str(self.root), "legacy.doc")

        self.assertEqual(result.content, "Converted via libreoffice")
        antiword_mock.assert_not_called()

    def test_read_doc_reports_clear_dependency_message_when_all_extractors_fail(self) -> None:
        doc_file = self.root / "legacy.doc"
        doc_file.write_bytes(b"legacy")

        with (
            patch.object(self.service, "_read_doc_with_word_com", side_effect=FileOperationError("word unavailable")),
            patch.object(self.service, "_read_doc_with_libreoffice", side_effect=FileOperationError("libreoffice missing")),
            patch.object(self.service, "_read_doc_with_antiword", side_effect=FileOperationError("antiword missing")),
        ):
            with self.assertRaises(FileOperationError) as context:
                self.service.read_file(str(self.root), "legacy.doc")

        message = str(context.exception).lower()
        self.assertIn("legacy .doc", message)
        self.assertIn("install at least one supported extractor", message)
        self.assertIn("libreoffice", message)
        self.assertIn("antiword", message)


if __name__ == "__main__":
    unittest.main()
