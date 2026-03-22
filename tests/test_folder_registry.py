from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from app.files.folder_registry import AllowedFolderRegistry
from app.security.path_guard import PathAccessError, PathGuard


class FolderRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE allowed_roots (
                path TEXT PRIMARY KEY,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            );
            """
        )
        self.registry = AllowedFolderRegistry(self.connection)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_add_and_list_folder(self) -> None:
        folder = self.registry.add_folder(self.temp_dir.name)
        self.assertEqual(self.registry.list_folders(), [folder])
        self.assertTrue(self.registry.is_allowed(folder))

    def test_remove_folder(self) -> None:
        folder = self.registry.add_folder(self.temp_dir.name)
        self.registry.remove_folder(folder)
        self.assertEqual(self.registry.list_folders(), [])

    def test_path_guard_rejects_outside_root(self) -> None:
        allowed_root = Path(self.temp_dir.name)
        inside = allowed_root / "inside.txt"
        outside = allowed_root.parent / "outside.txt"
        self.assertEqual(PathGuard.ensure_within_roots(inside, [allowed_root]), inside.resolve())
        with self.assertRaises(PathAccessError):
            PathGuard.ensure_within_roots(outside, [allowed_root])


if __name__ == "__main__":
    unittest.main()
