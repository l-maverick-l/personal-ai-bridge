from __future__ import annotations

import sqlite3
from pathlib import Path

APP_NAME = "PersonalAIBridge"
DB_FILENAME = "personal_ai_bridge.db"


def get_app_data_dir() -> Path:
    if Path.home().joinpath("AppData", "Roaming").exists():
        base = Path.home() / "AppData" / "Roaming"
    else:
        base = Path.home() / ".local" / "share"
    app_dir = base / APP_NAME
    app_dir.mkdir(parents=True, exist_ok=True)
    return app_dir


def get_database_path() -> Path:
    return get_app_data_dir() / DB_FILENAME


def connect_database() -> sqlite3.Connection:
    connection = sqlite3.connect(get_database_path())
    connection.row_factory = sqlite3.Row
    initialize_database(connection)
    return connection


def initialize_database(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS allowed_roots (
            path TEXT PRIMARY KEY,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS action_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            action_type TEXT NOT NULL,
            target TEXT NOT NULL,
            status TEXT NOT NULL,
            error_message TEXT DEFAULT ''
        );
        """
    )
    connection.commit()
