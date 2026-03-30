from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.data.database import connect_database
from app.files.folder_registry import AllowedFolderRegistry
from app.security.path_guard import PathAccessError, PathGuard


class FolderRegistryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        connection = connect_database(self.temp_db.name)
        connection.close()
        self.registry = AllowedFolderRegistry(self.temp_db.name)
        self.temp_dir = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        import os

        os.unlink(self.temp_db.name)
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
