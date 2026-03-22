from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from app.core.app_context import AppContext
from app.data.action_log import ActionLogger, configure_logging
from app.data.database import connect_database
from app.data.settings_store import SettingsStore
from app.files.folder_registry import AllowedFolderRegistry
from app.ui.main_window import MainWindow


def build_context() -> tuple[AppContext, object]:
    connection = connect_database()
    context = AppContext(
        settings_store=SettingsStore(connection),
        action_logger=ActionLogger(connection),
        folder_registry=AllowedFolderRegistry(connection),
    )
    return context, connection


def main() -> int:
    app = QApplication(sys.argv)
    logger = configure_logging()
    context, connection = build_context()
    window = MainWindow(context, logger)
    window.show()
    exit_code = app.exec()
    connection.close()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
