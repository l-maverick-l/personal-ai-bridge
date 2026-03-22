from __future__ import annotations

import sqlite3
from pathlib import Path

from app.security.path_guard import PathGuard


class AllowedFolderRegistry:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def list_folders(self) -> list[str]:
        rows = self._connection.execute(
            "SELECT path FROM allowed_roots ORDER BY path COLLATE NOCASE"
        ).fetchall()
        return [row["path"] for row in rows]

    def add_folder(self, folder_path: str) -> str:
        normalized = str(PathGuard.normalize(folder_path))
        if not Path(normalized).exists():
            raise FileNotFoundError(f"Folder does not exist: {normalized}")
        self._connection.execute(
            "INSERT OR IGNORE INTO allowed_roots (path) VALUES (?)",
            (normalized,),
        )
        self._connection.commit()
        return normalized

    def remove_folder(self, folder_path: str) -> None:
        normalized = str(PathGuard.normalize(folder_path))
        self._connection.execute("DELETE FROM allowed_roots WHERE path = ?", (normalized,))
        self._connection.commit()

    def is_allowed(self, folder_path: str) -> bool:
        normalized = str(PathGuard.normalize(folder_path))
        return normalized in self.list_folders()
