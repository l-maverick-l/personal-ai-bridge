from __future__ import annotations

import sqlite3
import unittest

from app.data.settings_store import SettingsStore
from app.models.settings import AppSettings, ProviderConfig


class SettingsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript(
            """
            CREATE TABLE settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        self.store = SettingsStore(self.connection)

    def test_load_returns_defaults_when_empty(self) -> None:
        settings = self.store.load()
        self.assertEqual(settings.ai_mode, "skip")
        self.assertEqual(settings.provider.provider_type, "none")

    def test_save_and_load_round_trip(self) -> None:
        expected = AppSettings(
            ai_mode="local",
            provider=ProviderConfig(
                provider_type="ollama",
                label="Ollama",
                base_url="http://127.0.0.1:11434",
                model_name="llama3",
                api_key="",
                local_only=True,
            ),
            yahoo_email="user@yahoo.com",
            yahoo_app_password="secret",
            setup_complete=True,
        )
        self.store.save(expected)

        actual = self.store.load()

        self.assertEqual(actual.ai_mode, expected.ai_mode)
        self.assertEqual(actual.provider.provider_type, expected.provider.provider_type)
        self.assertEqual(actual.provider.model_name, expected.provider.model_name)
        self.assertEqual(actual.yahoo_email, expected.yahoo_email)
        self.assertTrue(actual.setup_complete)


if __name__ == "__main__":
    unittest.main()
