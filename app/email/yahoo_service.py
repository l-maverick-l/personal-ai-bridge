from __future__ import annotations

import email
import imaplib
import re
import smtplib
import socket
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from email.message import EmailMessage, Message
from email.utils import formataddr, make_msgid, parsedate_to_datetime
from html.parser import HTMLParser
from typing import Callable

from app.ai.client import AIClient, AIClientError, AIUnavailableError
from app.data.action_log import ActionLogger
from app.data.settings_store import SettingsStore
from app.models.settings import AppSettings

DEFAULT_IMAP_SERVER = "imap.mail.yahoo.com"
DEFAULT_IMAP_PORT = 993
DEFAULT_SMTP_SERVER = "smtp.mail.yahoo.com"
DEFAULT_SMTP_PORT = 465
LIST_HEADERS = "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)] FLAGS)"
FULL_MESSAGE = "(RFC822)"


class YahooMailError(RuntimeError):
    """Raised for user-facing Yahoo Mail failures."""


@dataclass(slots=True)
class YahooConnectionResult:
    ok: bool
    message: str
    imap_ok: bool
    smtp_ok: bool


@dataclass(slots=True)
class MailSummary:
    uid: str
    subject: str
    sender: str
    received_at: str
    unread: bool


@dataclass(slots=True)
class MailMessageView:
    uid: str
    subject: str
    sender: str
    recipients: str
    received_at: str
    unread: bool
    body_text: str


@dataclass(slots=True)
class OutgoingDraft:
    to_address: str
    subject: str
    body: str
    in_reply_to: str = ""
    references: str = ""


class _HTMLTextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._chunks: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._chunks.append(data.strip())

    def text(self) -> str:
        return "\n".join(self._chunks)


class YahooMailService:
    def __init__(
        self,
        settings_store: SettingsStore,
        action_logger: ActionLogger,
        ai_client: AIClient,
        imap_factory: Callable[[str, int], imaplib.IMAP4_SSL] | None = None,
        smtp_factory: Callable[[str, int], smtplib.SMTP_SSL] | None = None,
    ) -> None:
        self._settings_store = settings_store
        self._action_logger = action_logger
        self._ai_client = ai_client
        self._imap_factory = imap_factory or imaplib.IMAP4_SSL
        self._smtp_factory = smtp_factory or smtplib.SMTP_SSL

    def connection_status_text(self) -> str:
        settings = self._settings_store.load()
        return "Configured" if settings.yahoo_is_configured() else "Not configured"

    def test_connection(self, settings: AppSettings | None = None) -> YahooConnectionResult:
        active_settings = settings or self._settings_store.load()
        self._validate_settings(active_settings)
        imap_ok = False
        smtp_ok = False
        messages: list[str] = []

        try:
            with self._imap_client(active_settings) as mailbox:
                mailbox.select("INBOX", readonly=True)
                imap_ok = True
                messages.append("IMAP login worked and INBOX opened.")
        except Exception as exc:
            user_error = self._user_error(exc)
            self._record("email_test_imap", active_settings.yahoo_email, "error", str(user_error))
            raise user_error from exc

        try:
            with self._smtp_client(active_settings) as smtp:
                smtp.login(active_settings.yahoo_email, active_settings.yahoo_app_password)
                smtp_ok = True
                messages.append("SMTP login worked.")
        except Exception as exc:
            user_error = self._user_error(exc)
            self._record("email_test_smtp", active_settings.yahoo_email, "error", str(user_error))
            raise user_error from exc

        self._record("email_test_imap", active_settings.yahoo_email, "success")
        self._record("email_test_smtp", active_settings.yahoo_email, "success")
        return YahooConnectionResult(
            ok=imap_ok and smtp_ok,
            message=" ".join(messages),
            imap_ok=imap_ok,
            smtp_ok=smtp_ok,
        )

    def list_inbox(
        self,
        unread_only: bool | None = None,
        sender: str = "",
        subject_keyword: str = "",
        start_date: date | None = None,
        end_date: date | None = None,
        limit: int = 25,
    ) -> list[MailSummary]:
        settings = self._settings_store.load()
        self._validate_settings(settings)
        search_criteria = self._build_search_criteria(
            unread_only=unread_only,
            sender=sender,
            subject_keyword=subject_keyword,
            start_date=start_date,
            end_date=end_date,
        )
        action_target = " ".join(search_criteria)
        try:
            with self._imap_client(settings) as mailbox:
                mailbox.select("INBOX", readonly=True)
                status, data = mailbox.uid("SEARCH", None, *search_criteria)
                self._ensure_ok(status, data, "Yahoo search failed")
                raw_ids = data[0].decode("utf-8") if data and data[0] else ""
                uids = [item for item in raw_ids.split() if item]
                uids = list(reversed(uids))[:limit]
                summaries: list[MailSummary] = []
                for uid in uids:
                    summaries.append(self._fetch_summary(mailbox, uid))
                self._record("email_list", action_target or "INBOX", "success")
                return summaries
        except Exception as exc:
            user_error = self._user_error(exc)
            self._record("email_list", action_target or "INBOX", "error", str(user_error))
            raise user_error from exc

    def read_email(self, uid: str) -> MailMessageView:
        settings = self._settings_store.load()
        self._validate_settings(settings)
        try:
            with self._imap_client(settings) as mailbox:
                mailbox.select("INBOX", readonly=True)
                status, data = mailbox.uid("FETCH", uid, FULL_MESSAGE)
                self._ensure_ok(status, data, "Could not download the selected email")
                message_bytes, flag_bytes = self._extract_fetch_parts(data)
                parsed = email.message_from_bytes(message_bytes)
                view = self._build_message_view(uid, parsed, flag_bytes)
                self._record("email_read", uid, "success")
                return view
        except Exception as exc:
            user_error = self._user_error(exc)
            self._record("email_read", uid, "error", str(user_error))
            raise user_error from exc

    def summarize_email(self, uid: str) -> str:
        message = self.read_email(uid)
        settings = self._settings_store.load()
        try:
            return self._ai_client.generate_text(
                settings,
                system_prompt="You summarize email messages for a non-technical user.",
                user_prompt=(
                    "Summarize this email in short bullets. Include the sender, main request, important dates, "
                    "and any action items.\n\n"
                    f"From: {message.sender}\n"
                    f"Subject: {message.subject}\n"
                    f"Received: {message.received_at}\n\n"
                    f"Body:\n{message.body_text[:12000]}"
                ),
            )
        except (AIUnavailableError, AIClientError) as exc:
            self._record("email_summarize", uid, "error", str(exc))
            raise YahooMailError(str(exc)) from exc

    def draft_reply(self, uid: str, user_notes: str = "") -> OutgoingDraft:
        message = self.read_email(uid)
        settings = self._settings_store.load()
        try:
            draft_text = self._ai_client.generate_text(
                settings,
                system_prompt="You draft clear, polite email replies for a non-technical user.",
                user_prompt=(
                    "Draft a reply email. Keep it practical and ready to send. "
                    "Do not invent facts that are not in the original message or user notes.\n\n"
                    f"Original sender: {message.sender}\n"
                    f"Original subject: {message.subject}\n"
                    f"Original body:\n{message.body_text[:10000]}\n\n"
                    f"User notes for the reply:\n{user_notes or 'No extra notes provided.'}"
                ),
            )
        except (AIUnavailableError, AIClientError) as exc:
            self._record("email_draft_reply", uid, "error", str(exc))
            raise YahooMailError(str(exc)) from exc
        reply_subject = message.subject if message.subject.lower().startswith("re:") else f"Re: {message.subject}"
        self._record("email_draft_reply", uid, "success")
        return OutgoingDraft(
            to_address=self._extract_address(message.sender),
            subject=reply_subject,
            body=draft_text,
        )

    def draft_new_email(self, to_address: str, subject: str, user_notes: str) -> OutgoingDraft:
        settings = self._settings_store.load()
        try:
            draft_text = self._ai_client.generate_text(
                settings,
                system_prompt="You draft clear, friendly outbound emails for a non-technical user.",
                user_prompt=(
                    "Write a new email draft that the user can review and edit before sending. "
                    "Keep it clear, specific, and not overly formal.\n\n"
                    f"To: {to_address or 'Not specified yet'}\n"
                    f"Subject: {subject or 'Not specified yet'}\n"
                    f"What the user wants to say:\n{user_notes or 'No notes provided.'}"
                ),
            )
        except (AIUnavailableError, AIClientError) as exc:
            self._record("email_draft_new", to_address or subject or "new", "error", str(exc))
            raise YahooMailError(str(exc)) from exc
        self._record("email_draft_new", to_address or subject or "new", "success")
        return OutgoingDraft(to_address=to_address.strip(), subject=subject.strip(), body=draft_text)

    def send_email(self, draft: OutgoingDraft) -> None:
        settings = self._settings_store.load()
        self._validate_settings(settings)
        if not draft.to_address.strip():
            raise YahooMailError("Enter a recipient email address before sending.")
        if not draft.subject.strip():
            raise YahooMailError("Enter an email subject before sending.")
        if not draft.body.strip():
            raise YahooMailError("The draft body is empty. Review the email before sending.")

        message = EmailMessage()
        message["From"] = formataddr((settings.yahoo_email, settings.yahoo_email))
        message["To"] = draft.to_address.strip()
        message["Subject"] = draft.subject.strip()
        message["Message-ID"] = make_msgid()
        if draft.in_reply_to:
            message["In-Reply-To"] = draft.in_reply_to
        if draft.references:
            message["References"] = draft.references
        message.set_content(draft.body)

        try:
            with self._smtp_client(settings) as smtp:
                smtp.login(settings.yahoo_email, settings.yahoo_app_password)
                smtp.send_message(message)
            self._record("email_send", draft.to_address, "success")
        except Exception as exc:
            user_error = self._user_error(exc)
            self._record("email_send", draft.to_address, "error", str(user_error))
            raise user_error from exc

    def _fetch_summary(self, mailbox: imaplib.IMAP4_SSL, uid: str) -> MailSummary:
        status, data = mailbox.uid("FETCH", uid, LIST_HEADERS)
        self._ensure_ok(status, data, "Could not load message headers")
        header_bytes, flag_bytes = self._extract_fetch_parts(data)
        parsed = email.message_from_bytes(header_bytes)
        received = self._safe_date(parsed.get("Date", ""))
        return MailSummary(
            uid=uid,
            subject=self._decode_header_value(parsed.get("Subject", "(No subject)")),
            sender=self._decode_header_value(parsed.get("From", "(Unknown sender)")),
            received_at=received,
            unread=b"\\Seen" not in flag_bytes,
        )

    def _build_message_view(self, uid: str, parsed: Message, flag_bytes: bytes) -> MailMessageView:
        return MailMessageView(
            uid=uid,
            subject=self._decode_header_value(parsed.get("Subject", "(No subject)")),
            sender=self._decode_header_value(parsed.get("From", "(Unknown sender)")),
            recipients=self._decode_header_value(parsed.get("To", "")),
            received_at=self._safe_date(parsed.get("Date", "")),
            unread=b"\\Seen" not in flag_bytes,
            body_text=self._extract_body_text(parsed),
        )

    def _build_search_criteria(
        self,
        unread_only: bool | None,
        sender: str,
        subject_keyword: str,
        start_date: date | None,
        end_date: date | None,
    ) -> list[str]:
        criteria = ["ALL"]
        if unread_only is True:
            criteria.append("UNSEEN")
        elif unread_only is False:
            criteria.append("SEEN")
        if sender.strip():
            criteria.extend(["FROM", f'"{sender.strip()}"'])
        if subject_keyword.strip():
            criteria.extend(["SUBJECT", f'"{subject_keyword.strip()}"'])
        if start_date:
            criteria.extend(["SINCE", start_date.strftime("%d-%b-%Y")])
        if end_date:
            criteria.extend(["BEFORE", (end_date + timedelta(days=1)).strftime("%d-%b-%Y")])
        return criteria

    def _imap_client(self, settings: AppSettings) -> imaplib.IMAP4_SSL:
        client = self._imap_factory(
            settings.yahoo_imap_server or DEFAULT_IMAP_SERVER,
            int(settings.yahoo_imap_port or DEFAULT_IMAP_PORT),
        )
        client.login(settings.yahoo_email, settings.yahoo_app_password)
        return client

    def _smtp_client(self, settings: AppSettings) -> smtplib.SMTP_SSL:
        return self._smtp_factory(
            settings.yahoo_smtp_server or DEFAULT_SMTP_SERVER,
            int(settings.yahoo_smtp_port or DEFAULT_SMTP_PORT),
        )

    def _extract_fetch_parts(self, data: list[object]) -> tuple[bytes, bytes]:
        message_bytes = b""
        flag_bytes = b""
        for item in data:
            if isinstance(item, tuple):
                if len(item) >= 2 and isinstance(item[1], bytes):
                    message_bytes = item[1]
                if isinstance(item[0], bytes):
                    flag_bytes += item[0]
            elif isinstance(item, bytes):
                flag_bytes += item
        if not message_bytes:
            raise YahooMailError("Yahoo returned an empty message payload.")
        return message_bytes, flag_bytes

    def _extract_body_text(self, message: Message) -> str:
        if message.is_multipart():
            plain_parts: list[str] = []
            html_parts: list[str] = []
            for part in message.walk():
                if part.get_content_disposition() == "attachment":
                    continue
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or "utf-8"
                content = payload.decode(charset, errors="replace")
                content_type = part.get_content_type()
                if content_type == "text/plain":
                    plain_parts.append(content)
                elif content_type == "text/html":
                    html_parts.append(content)
            if plain_parts:
                return "\n\n".join(self._normalize_text(part) for part in plain_parts).strip()
            if html_parts:
                extractor = _HTMLTextExtractor()
                for html_part in html_parts:
                    extractor.feed(html_part)
                return self._normalize_text(extractor.text())
            return ""
        payload = message.get_payload(decode=True)
        if payload is None:
            return ""
        charset = message.get_content_charset() or "utf-8"
        content = payload.decode(charset, errors="replace")
        if message.get_content_type() == "text/html":
            extractor = _HTMLTextExtractor()
            extractor.feed(content)
            return self._normalize_text(extractor.text())
        return self._normalize_text(content)

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"\n{3,}", "\n\n", text.replace("\r\n", "\n")).strip()

    def _decode_header_value(self, value: str) -> str:
        return str(email.header.make_header(email.header.decode_header(value))) if value else ""

    def _safe_date(self, value: str) -> str:
        if not value:
            return "Unknown date"
        try:
            parsed = parsedate_to_datetime(value)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=datetime.now().astimezone().tzinfo)
            return parsed.astimezone().strftime("%Y-%m-%d %H:%M %Z")
        except Exception:
            return value

    def _extract_address(self, sender: str) -> str:
        match = re.search(r"<([^>]+)>", sender)
        return match.group(1).strip() if match else sender.strip()

    def _ensure_ok(self, status: str, data: list[object], fallback_message: str) -> None:
        if status != "OK":
            details = data[0].decode("utf-8", errors="replace") if data and isinstance(data[0], bytes) else fallback_message
            raise YahooMailError(details or fallback_message)

    def _validate_settings(self, settings: AppSettings) -> None:
        if not settings.yahoo_email.strip():
            raise YahooMailError("Enter your Yahoo email address in settings before using Yahoo Mail.")
        if not settings.yahoo_app_password:
            raise YahooMailError(
                "Enter your Yahoo app password in settings. Yahoo Mail does not allow the normal account password here."
            )

    def _record(self, action_type: str, target: str, status: str, error_message: str = "") -> None:
        self._action_logger.record(action_type, target, status, error_message)

    def _user_error(self, exc: Exception) -> YahooMailError:
        message = str(exc).strip() or exc.__class__.__name__
        lowered = message.lower()
        if isinstance(exc, YahooMailError):
            return exc
        if isinstance(exc, socket.gaierror):
            return YahooMailError(
                "Could not reach Yahoo Mail. Check your internet connection and the Yahoo server names in settings."
            )
        if isinstance(exc, TimeoutError):
            return YahooMailError("Yahoo Mail timed out. Check your internet connection and try again.")
        if isinstance(exc, (imaplib.IMAP4.abort, imaplib.IMAP4.readonly)):
            return YahooMailError("Yahoo Mail closed the connection unexpectedly. Please try again.")
        if isinstance(exc, imaplib.IMAP4.error):
            if "application-specific password" in lowered or "app password" in lowered:
                return YahooMailError(
                    "Yahoo rejected the login. Use a Yahoo app password, not your regular Yahoo password."
                )
            if "invalid credentials" in lowered or "authenticationfailed" in lowered or "auth" in lowered:
                return YahooMailError(
                    "Yahoo rejected the login. Check the Yahoo email address and app password, then try again."
                )
            return YahooMailError(f"Yahoo IMAP error: {message}")
        if isinstance(exc, smtplib.SMTPAuthenticationError):
            return YahooMailError(
                "Yahoo rejected the SMTP login. Check the Yahoo email address and app password."
            )
        if isinstance(exc, smtplib.SMTPConnectError):
            return YahooMailError(
                "Could not connect to Yahoo SMTP. Check your internet connection and SMTP server settings."
            )
        if isinstance(exc, smtplib.SMTPRecipientsRefused):
            return YahooMailError("Yahoo rejected the recipient address. Check the recipient email and try again.")
        if isinstance(exc, smtplib.SMTPException):
            return YahooMailError(f"Yahoo SMTP error: {message}")
        if isinstance(exc, (ConnectionError, OSError)):
            return YahooMailError(
                "Could not connect to Yahoo Mail. Check your internet connection and Yahoo settings, then try again."
            )
        return YahooMailError(message)
