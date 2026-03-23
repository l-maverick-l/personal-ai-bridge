from __future__ import annotations

import sys

from app.ai.client import AIClient
from app.core.app_context import AppContext
from app.data.action_log import ActionLogger, configure_logging
from app.data.database import connect_database
from app.data.settings_store import SettingsStore
from app.email.yahoo_service import YahooMailService
from app.files.folder_registry import AllowedFolderRegistry
from app.files.service import FileService


def build_context() -> tuple[AppContext, object]:
    connection = connect_database()
    settings_store = SettingsStore(connection)
    action_logger = ActionLogger(connection)
    folder_registry = AllowedFolderRegistry(connection)
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
    return context, connection


def main() -> int:
    from PySide6.QtWidgets import QApplication

    from app.ui.main_window import MainWindow

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
