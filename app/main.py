from __future__ import annotations

import sys

from app.ai.client import AIClient
from app.core.app_context import AppContext
from app.data.action_log import ActionLogger, configure_logging
from app.data.database import connect_database, get_database_path
from app.data.settings_store import SettingsStore
from app.email.yahoo_service import YahooMailService
from app.files.folder_registry import AllowedFolderRegistry
from app.files.service import FileService


def build_context() -> AppContext:
    database_path = get_database_path()
    connection = connect_database(database_path)
    connection.close()
    settings_store = SettingsStore(database_path)
    action_logger = ActionLogger(database_path)
    folder_registry = AllowedFolderRegistry(database_path)
    ai_client = AIClient()
    yahoo_mail_service = YahooMailService(
        settings_store=settings_store,
        action_logger=action_logger,
        ai_client=ai_client,
    )
    context = AppContext(
        settings_store=settings_store,
        action_logger=action_logger,
        folder_registry=folder_registry,
        file_service=FileService(
            folder_registry=folder_registry,
            action_logger=action_logger,
            settings_store=settings_store,
            ai_client=ai_client,
        ),
        ai_client=ai_client,
        yahoo_mail_service=yahoo_mail_service,
    )
    return context


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app.ui.main_window import MainWindow

    app = QApplication(sys.argv)
    logger = configure_logging()
    context = build_context()
    window = MainWindow(context, logger)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
