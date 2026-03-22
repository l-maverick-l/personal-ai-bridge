from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from app.data.database import APP_NAME, get_app_data_dir

LOGGER_NAME = APP_NAME


class ActionLogger:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def record(self, action_type: str, target: str, status: str, error_message: str = "") -> None:
        self._connection.execute(
            """
            INSERT INTO action_log (action_type, target, status, error_message)
            VALUES (?, ?, ?, ?)
            """,
            (action_type, target, status, error_message),
        )
        self._connection.commit()

    def recent_entries(self, limit: int = 20) -> list[sqlite3.Row]:
        rows = self._connection.execute(
            """
            SELECT timestamp, action_type, target, status, error_message
            FROM action_log
            ORDER BY id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return list(rows)


def configure_logging() -> logging.Logger:
    app_dir = get_app_data_dir()
    logger = logging.getLogger(LOGGER_NAME)
    if logger.handlers:
        return logger

    logger.setLevel(logging.INFO)
    log_file = Path(app_dir) / "personal_ai_bridge.log"
    handler = logging.FileHandler(log_file, encoding="utf-8")
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    )
    logger.addHandler(handler)
    logger.propagate = False
    logger.info("Logging initialized")
    return logger
