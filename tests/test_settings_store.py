from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import tempfile
import unittest

from app.data.database import connect_database
from app.data.settings_store import SettingsStore
from app.models.settings import AppSettings, ProviderConfig


class SettingsStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        connection = connect_database(self.temp_db.name)
        connection.close()
        self.store = SettingsStore(self.temp_db.name)

    def tearDown(self) -> None:
        import os

        os.unlink(self.temp_db.name)

    def test_load_returns_defaults_when_empty(self) -> None:
        settings = self.store.load()
        self.assertEqual(settings.ai_mode, "skip")
        self.assertEqual(settings.provider.provider_type, "none")
        self.assertEqual(settings.yahoo_imap_server, "imap.mail.yahoo.com")
        self.assertEqual(settings.yahoo_smtp_port, 465)
        self.assertFalse(settings.assistant_always_on_top)
        self.assertFalse(settings.assistant_compact_mode)

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
            yahoo_imap_server="imap.mail.yahoo.com",
            yahoo_imap_port=993,
            yahoo_smtp_server="smtp.mail.yahoo.com",
            yahoo_smtp_port=465,
            assistant_always_on_top=True,
            assistant_compact_mode=True,
            setup_complete=True,
        )
        self.store.save(expected)

        actual = self.store.load()

        self.assertEqual(actual.ai_mode, expected.ai_mode)
        self.assertEqual(actual.provider.provider_type, expected.provider.provider_type)
        self.assertEqual(actual.provider.model_name, expected.provider.model_name)
        self.assertEqual(actual.yahoo_email, expected.yahoo_email)
        self.assertEqual(actual.yahoo_imap_port, expected.yahoo_imap_port)
        self.assertEqual(actual.yahoo_smtp_server, expected.yahoo_smtp_server)
        self.assertTrue(actual.assistant_always_on_top)
        self.assertTrue(actual.assistant_compact_mode)
        self.assertTrue(actual.setup_complete)

    def test_store_can_be_used_from_worker_thread(self) -> None:
        expected = AppSettings(yahoo_email="thread-user@yahoo.com", setup_complete=True)
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(self.store.save, expected).result()
            loaded = executor.submit(self.store.load).result()
        self.assertEqual(loaded.yahoo_email, expected.yahoo_email)


if __name__ == "__main__":
    unittest.main()
