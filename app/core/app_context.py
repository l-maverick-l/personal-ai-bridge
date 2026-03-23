from __future__ import annotations

from dataclasses import dataclass

from app.ai.client import AIClient
from app.data.action_log import ActionLogger
from app.data.settings_store import SettingsStore
from app.email.yahoo_service import YahooMailService
from app.files.folder_registry import AllowedFolderRegistry
from app.files.service import FileService


@dataclass(slots=True)
class AppContext:
    settings_store: SettingsStore
    action_logger: ActionLogger
    folder_registry: AllowedFolderRegistry
    file_service: FileService
    ai_client: AIClient
    yahoo_mail_service: YahooMailService
