from __future__ import annotations

from dataclasses import dataclass

from app.data.action_log import ActionLogger
from app.data.settings_store import SettingsStore
from app.files.folder_registry import AllowedFolderRegistry


@dataclass(slots=True)
class AppContext:
    settings_store: SettingsStore
    action_logger: ActionLogger
    folder_registry: AllowedFolderRegistry
