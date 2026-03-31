from __future__ import annotations

import os
import tempfile
import unittest
from email.message import EmailMessage
from urllib.request import Request

from app.ai.client import AIClient
from app.data.action_log import ActionLogger
from app.data.database import connect_database
from app.data.settings_store import SettingsStore
from app.email.yahoo_service import OutgoingDraft, YahooMailError, YahooMailService
from app.models.settings import AppSettings, ProviderConfig


class FakeAIClient(AIClient):
    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple[str, str]] = []
        self.next_response = "Generated draft or summary"

    def is_available(self, settings: AppSettings) -> bool:
        return settings.ai_mode != "skip"

    def generate_text(
        self,
        settings: AppSettings,
        system_prompt: str,
        user_prompt: str,
        on_status=None,
        on_partial=None,
        is_cancelled=None,
    ) -> str:
        _ = on_status, on_partial, is_cancelled
        self.calls.append((system_prompt, user_prompt))
        if not self.is_available(settings):
            raise Exception("AI is not configured")
        return self.next_response

    def generate_final_text(
        self,
        settings: AppSettings,
        system_prompt: str,
        user_prompt: str,
        on_status=None,
        on_partial=None,
        is_cancelled=None,
    ) -> str:
        return self.generate_text(
            settings=settings,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            on_status=on_status,
            on_partial=on_partial,
            is_cancelled=is_cancelled,
        )


class FakeIMAP:
    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.login_args: tuple[str, str] | None = None
        self.search_args: tuple[str, ...] | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.logout()

    def login(self, username: str, password: str) -> tuple[str, list[bytes]]:
        self.login_args = (username, password)
        if password == "bad-app-password":
            raise AssertionError("Should not be used in the success path")
        return "OK", [b"logged in"]

    def select(self, mailbox: str, readonly: bool = True) -> tuple[str, list[bytes]]:
        return "OK", [b"1"]

    def uid(self, command: str, *args: str):
        if command == "SEARCH":
            self.search_args = args
            return "OK", [b"100 200"]
        if command == "FETCH":
            uid = args[0]
            if uid == "200":
                return "OK", [
                    (b'200 (FLAGS ())', self._build_message_bytes(subject="Project update", sender="Alice <alice@example.com>", body="Please review the attached plan.")),
                    b')',
                ]
            return "OK", [
                (b'100 (FLAGS (\\Seen))', self._build_message_bytes(subject="Old note", sender="Bob <bob@example.com>", body="Already read.")),
                b')',
            ]
        raise AssertionError(f"Unexpected IMAP command: {command}")

    def logout(self) -> tuple[str, list[bytes]]:
        return "BYE", [b"logout"]

    def _build_message_bytes(self, subject: str, sender: str, body: str) -> bytes:
        message = EmailMessage()
        message["From"] = sender
        message["To"] = "user@yahoo.com"
        message["Subject"] = subject
        message["Date"] = "Tue, 18 Mar 2025 10:00:00 +0000"
        message.set_content(body)
        return message.as_bytes()


class FakeSMTP:
    sent_messages: list[EmailMessage] = []

    def __init__(self, host: str, port: int) -> None:
        self.host = host
        self.port = port
        self.login_args: tuple[str, str] | None = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.quit()

    def login(self, username: str, password: str) -> tuple[int, bytes]:
        self.login_args = (username, password)
        return 235, b"ok"

    def send_message(self, message: EmailMessage) -> None:
        self.__class__.sent_messages.append(message)

    def quit(self) -> tuple[int, bytes]:
        return 221, b"bye"


class FailingIMAP(FakeIMAP):
    def login(self, username: str, password: str):
        raise YahooMailError(
            "Yahoo rejected the login. Use a Yahoo app password, not your regular Yahoo password."
        )


class FakeHTTPResponse:
    def __init__(self, payload: bytes, content_type: str) -> None:
        self._payload = payload
        self.headers = {"Content-Type": content_type}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def read(self, size: int = -1) -> bytes:
        if size < 0:
            return self._payload
        return self._payload[:size]


def fake_remote_opener(request: Request, timeout_seconds: float):
    _ = timeout_seconds
    url = request.full_url
    if "pixel.png" in url:
        return FakeHTTPResponse(b"\x89PNGfake", "image/png")
    raise OSError("not found")


class YahooMailServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        connection = connect_database(self.temp_db.name)
        connection.close()
        self.settings_store = SettingsStore(self.temp_db.name)
        self.settings_store.save(
            AppSettings(
                ai_mode="local",
                provider=ProviderConfig(provider_type="ollama", label="Ollama", base_url="http://localhost", model_name="tiny"),
                yahoo_email="user@yahoo.com",
                yahoo_app_password="app-password",
                setup_complete=True,
            )
        )
        self.logger = ActionLogger(self.temp_db.name)
        self.ai_client = FakeAIClient()
        FakeSMTP.sent_messages = []
        self.service = YahooMailService(
            settings_store=self.settings_store,
            action_logger=self.logger,
            ai_client=self.ai_client,
            imap_factory=FakeIMAP,
            smtp_factory=FakeSMTP,
            remote_url_opener=fake_remote_opener,
        )

    def tearDown(self) -> None:
        os.unlink(self.temp_db.name)

    def test_connection_and_inbox_listing_work(self) -> None:
        result = self.service.test_connection()
        self.assertTrue(result.ok)
        self.assertIn("IMAP login worked", result.message)

        messages = self.service.list_inbox(
            unread_only=True,
            sender="alice@example.com",
            subject_keyword="project",
        )

        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0].uid, "200")
        self.assertTrue(messages[0].unread)
        self.assertEqual(messages[0].subject, "Project update")

    def test_read_summarize_and_draft_email(self) -> None:
        message = self.service.read_email("200")
        self.assertIn("Please review", message.body_text)
        self.assertTrue(message.body_html)
        self.assertEqual(message.inline_images, [])
        self.assertEqual(message.attachments, [])
        self.assertEqual(message.sender, "Alice <alice@example.com>")

        summary = self.service.summarize_email("200")
        self.assertEqual(summary, "Generated draft or summary")

        reply_draft = self.service.draft_reply("200", "Say I will answer tomorrow.")
        self.assertEqual(reply_draft.to_address, "alice@example.com")
        self.assertTrue(reply_draft.subject.startswith("Re:"))

        new_draft = self.service.draft_new_email("team@example.com", "Hello", "Share the update")
        self.assertEqual(new_draft.body, "Generated draft or summary")
        self.assertEqual(len(self.ai_client.calls), 3)

    def test_draft_generation_rejects_json_only_output(self) -> None:
        self.ai_client.next_response = '{"draft":"hello"}'
        with self.assertRaises(YahooMailError) as ctx:
            self.service.draft_new_email("team@example.com", "Hello", "Share the update")
        self.assertIn("malformed", str(ctx.exception).lower())

    def test_html_and_inline_image_rendering(self) -> None:
        message = EmailMessage()
        message["From"] = "Example <example@example.com>"
        message["To"] = "user@yahoo.com"
        message["Subject"] = "HTML sample"
        message["Date"] = "Tue, 18 Mar 2025 10:00:00 +0000"
        message.set_content("Plain body fallback.")
        message.add_alternative(
            '<html><body><p>Hello <a href="https://example.com">there</a></p>'
            '<img src="cid:hero-image"><img src="https://tracker.example.com/pixel.png"></body></html>',
            subtype="html",
        )
        html_part = message.get_payload()[1]
        html_part.add_related(
            b"fake image bytes",
            maintype="image",
            subtype="png",
            cid="<hero-image>",
            disposition="inline",
            filename="hero.png",
        )

        view = self.service._build_message_view("999", message, b"")
        self.assertIn("data:image/png;base64", view.body_html)
        self.assertEqual(len(view.inline_images), 1)

        blocked = self.service.build_safe_preview_html(view, allow_remote_images=False)
        self.assertIn("https://example.com", blocked)
        self.assertNotIn("tracker.example.com", blocked)

        allowed = self.service.build_safe_preview_html(view, allow_remote_images=True)
        self.assertIn("data:image/png;base64", allowed)
        self.assertNotIn("tracker.example.com", allowed)

    def test_preview_keeps_safe_inline_styles(self) -> None:
        message = EmailMessage()
        message["From"] = "Example <example@example.com>"
        message["To"] = "user@yahoo.com"
        message["Subject"] = "Style sample"
        message["Date"] = "Tue, 18 Mar 2025 10:00:00 +0000"
        message.add_alternative(
            '<html><body><div style="width:600px;background-color:#eee;position:fixed;'
            'background-image:url(https://bad.example/x.png);padding:12px">Hello</div></body></html>',
            subtype="html",
        )

        view = self.service._build_message_view("777", message, b"")
        preview = self.service.build_safe_preview_html(view, allow_remote_images=False)
        self.assertIn('style="width: 600px; background-color: #eee; padding: 12px"', preview)
        self.assertNotIn("position:fixed", preview)
        self.assertNotIn("background-image", preview)

    def test_send_email_uses_smtp(self) -> None:
        self.service.send_email(
            OutgoingDraft(
                to_address="friend@example.com",
                subject="Checking in",
                body="Hello there",
            )
        )

        self.assertEqual(len(FakeSMTP.sent_messages), 1)
        sent = FakeSMTP.sent_messages[0]
        self.assertEqual(sent["To"], "friend@example.com")
        self.assertEqual(sent["Subject"], "Checking in")

    def test_missing_app_password_is_reported_clearly(self) -> None:
        self.service = YahooMailService(
            settings_store=self.settings_store,
            action_logger=self.logger,
            ai_client=self.ai_client,
            imap_factory=FailingIMAP,
            smtp_factory=FakeSMTP,
        )

        with self.assertRaises(YahooMailError) as context:
            self.service.test_connection()

        self.assertIn("app password", str(context.exception).lower())


if __name__ == "__main__":
    unittest.main()
