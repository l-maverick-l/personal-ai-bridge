from __future__ import annotations

import json
import sqlite3
from typing import Any

from app.models.settings import AppSettings

SETTINGS_KEY = "app_settings"


class SettingsStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def load(self) -> AppSettings:
        row = self._connection.execute(
            "SELECT value FROM settings WHERE key = ?", (SETTINGS_KEY,)
        ).fetchone()
        if not row:
            return AppSettings()
        payload: dict[str, Any] = json.loads(row["value"])
        return AppSettings.from_dict(payload)

    def save(self, settings: AppSettings) -> None:
        self._connection.execute(
            """
            INSERT INTO settings (key, value)
            VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (SETTINGS_KEY, json.dumps(settings.to_dict())),
        )
        self._connection.commit()
