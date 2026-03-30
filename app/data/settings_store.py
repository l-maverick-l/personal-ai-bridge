from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.data.database import open_database_connection
from app.models.settings import AppSettings

SETTINGS_KEY = "app_settings"


class SettingsStore:
    def __init__(self, database_path: Path | str) -> None:
        self._database_path = Path(database_path)

    def load(self) -> AppSettings:
        with open_database_connection(self._database_path) as connection:
            row = connection.execute(
                "SELECT value FROM settings WHERE key = ?", (SETTINGS_KEY,)
            ).fetchone()
        if not row:
            return AppSettings()
        payload: dict[str, Any] = json.loads(row["value"])
        return AppSettings.from_dict(payload)

    def save(self, settings: AppSettings) -> None:
        with open_database_connection(self._database_path) as connection:
            connection.execute(
                """
                INSERT INTO settings (key, value)
                VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (SETTINGS_KEY, json.dumps(settings.to_dict())),
            )
            connection.commit()
