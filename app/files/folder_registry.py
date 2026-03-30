from __future__ import annotations

from pathlib import Path

from app.data.database import open_database_connection
from app.security.path_guard import PathGuard


class AllowedFolderRegistry:
    def __init__(self, database_path: Path | str) -> None:
        self._database_path = Path(database_path)

    def list_folders(self) -> list[str]:
        with open_database_connection(self._database_path) as connection:
            rows = connection.execute(
                "SELECT path FROM allowed_roots ORDER BY path COLLATE NOCASE"
            ).fetchall()
        return [row["path"] for row in rows]

    def add_folder(self, folder_path: str) -> str:
        normalized = str(PathGuard.normalize(folder_path))
        if not Path(normalized).exists():
            raise FileNotFoundError(f"Folder does not exist: {normalized}")
        with open_database_connection(self._database_path) as connection:
            connection.execute(
                "INSERT OR IGNORE INTO allowed_roots (path) VALUES (?)",
                (normalized,),
            )
            connection.commit()
        return normalized

    def remove_folder(self, folder_path: str) -> None:
        normalized = str(PathGuard.normalize(folder_path))
        with open_database_connection(self._database_path) as connection:
            connection.execute("DELETE FROM allowed_roots WHERE path = ?", (normalized,))
            connection.commit()

    def is_allowed(self, folder_path: str) -> bool:
        normalized = str(PathGuard.normalize(folder_path))
        return normalized in self.list_folders()
