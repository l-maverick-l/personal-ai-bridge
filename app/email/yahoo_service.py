from __future__ import annotations

import base64
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
from urllib.error import URLError
from urllib.request import Request, urlopen
from urllib.parse import urlsplit

from app.ai.client import AIClient, AIClientError, AIModelOutputError, AITimeoutError, AIUnavailableError
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
class InlineImageMeta:
    content_id: str
    content_type: str
    filename: str
    size: int


@dataclass(slots=True)
class AttachmentMeta:
    filename: str
    content_type: str
    size: int
    disposition: str


@dataclass(slots=True)
class MailMessageView:
    uid: str
    subject: str
    sender: str
    recipients: str
    received_at: str
    unread: bool
    body_text: str
    body_html: str
    inline_images: list[InlineImageMeta]
    attachments: list[AttachmentMeta]


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


class _SafeHTMLSanitizer(HTMLParser):
    _allowed_tags = {
        "a",
        "p",
        "br",
        "div",
        "span",
        "strong",
        "b",
        "em",
        "i",
        "u",
        "blockquote",
        "pre",
        "code",
        "ul",
        "ol",
        "li",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "table",
        "thead",
        "tbody",
        "tr",
        "th",
        "td",
        "img",
        "hr",
    }
    _allowed_attrs = {
        "a": {"href", "title"},
        "img": {"src", "alt", "title", "width", "height", "style"},
        "td": {"colspan", "rowspan", "align"},
        "th": {"colspan", "rowspan", "align"},
        "p": {"align", "style"},
        "div": {"align", "style"},
        "span": {"style"},
        "table": {"border", "cellpadding", "cellspacing", "width", "style"},
        "tbody": {"style"},
        "thead": {"style"},
        "tr": {"style"},
    }

    _allowed_style_props = {
        "background",
        "background-color",
        "border",
        "border-bottom",
        "border-collapse",
        "border-left",
        "border-right",
        "border-top",
        "color",
        "display",
        "font-family",
        "font-size",
        "font-style",
        "font-weight",
        "height",
        "line-height",
        "margin",
        "margin-bottom",
        "margin-left",
        "margin-right",
        "margin-top",
        "max-height",
        "max-width",
        "min-height",
        "min-width",
        "padding",
        "padding-bottom",
        "padding-left",
        "padding-right",
        "padding-top",
        "text-align",
        "text-decoration",
        "vertical-align",
        "white-space",
        "width",
    }

    def __init__(self, allow_remote_images: bool, remote_image_loader: "_RemoteImageLoader | None" = None) -> None:
        super().__init__(convert_charrefs=True)
        self._allow_remote_images = allow_remote_images
        self._remote_image_loader = remote_image_loader
        self._chunks: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "iframe", "object", "embed"}:
            self._skip_depth += 1
            return
        if self._skip_depth > 0 or lowered not in self._allowed_tags:
            return

        allowed = self._allowed_attrs.get(lowered, set())
        kept_attrs: list[str] = []
        for name, raw_value in attrs:
            attr = (name or "").lower()
            value = raw_value or ""
            if attr.startswith("on"):
                continue
            if attr not in allowed:
                continue
            if attr == "href" and not self._is_safe_href(value):
                continue
            if attr == "style":
                style_value = self._sanitize_style(value)
                if not style_value:
                    continue
                escaped = self._escape(style_value)
                kept_attrs.append(f'{attr}="{escaped}"')
                continue
            if attr == "src":
                if not self._is_safe_img_src(value):
                    continue
                if self._is_remote(value) and not self._allow_remote_images:
                    continue
                if self._is_remote(value) and self._allow_remote_images and self._remote_image_loader:
                    data_url = self._remote_image_loader.load(value)
                    if not data_url:
                        continue
                    value = data_url
            escaped = self._escape(value)
            kept_attrs.append(f'{attr}="{escaped}"')

        attrs_text = f" {' '.join(kept_attrs)}" if kept_attrs else ""
        self._chunks.append(f"<{lowered}{attrs_text}>")

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in {"script", "style", "iframe", "object", "embed"} and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if self._skip_depth > 0 or lowered not in self._allowed_tags:
            return
        self._chunks.append(f"</{lowered}>")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(self._escape(data))

    def handle_entityref(self, name: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self._skip_depth == 0:
            self._chunks.append(f"&#{name};")

    def html(self) -> str:
        return "".join(self._chunks)

    def _is_safe_href(self, value: str) -> bool:
        parsed = urlsplit(value.strip())
        scheme = parsed.scheme.lower()
        return scheme in {"", "http", "https", "mailto"}

    def _is_safe_img_src(self, value: str) -> bool:
        parsed = urlsplit(value.strip())
        scheme = parsed.scheme.lower()
        if scheme in {"", "http", "https", "data"}:
            return True
        return False

    def _is_remote(self, value: str) -> bool:
        scheme = urlsplit(value.strip()).scheme.lower()
        return scheme in {"http", "https"}

    def _escape(self, value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )

    def _sanitize_style(self, value: str) -> str:
        cleaned: list[str] = []
        for declaration in value.split(";"):
            if ":" not in declaration:
                continue
            raw_prop, raw_val = declaration.split(":", 1)
            prop = raw_prop.strip().lower()
            if prop not in self._allowed_style_props:
                continue
            style_value = raw_val.strip()
            if not style_value:
                continue
            lowered = style_value.lower()
            if "url(" in lowered or "expression(" in lowered or "@import" in lowered or "javascript:" in lowered:
                continue
            if re.search(r"[^a-zA-Z0-9#%(),.\-+/\s'\"]", style_value):
                continue
            cleaned.append(f"{prop}: {style_value}")
        return "; ".join(cleaned)


class _RemoteImageLoader:
    def __init__(
        self,
        url_opener: Callable[[Request, float], object],
        timeout_seconds: float = 4.0,
        max_images: int = 24,
        max_bytes_per_image: int = 2_000_000,
    ) -> None:
        self._url_opener = url_opener
        self._timeout_seconds = timeout_seconds
        self._max_images = max_images
        self._max_bytes_per_image = max_bytes_per_image
        self._cache: dict[str, str | None] = {}
        self._attempted = 0

    def load(self, url: str) -> str | None:
        normalized = url.strip()
        if not normalized:
            return None
        if normalized in self._cache:
            return self._cache[normalized]
        if self._attempted >= self._max_images:
            self._cache[normalized] = None
            return None
        self._attempted += 1
        self._cache[normalized] = self._download_as_data_url(normalized)
        return self._cache[normalized]

    def _download_as_data_url(self, url: str) -> str | None:
        request = Request(
            url,
            headers={
                "User-Agent": "PersonalAIBridge/1.0 (email-preview)",
                "Accept": "image/*,*/*;q=0.2",
            },
        )
        try:
            with self._url_opener(request, self._timeout_seconds) as response:
                payload = response.read(self._max_bytes_per_image + 1)
                if len(payload) > self._max_bytes_per_image:
                    return None
                content_type = getattr(response, "headers", {}).get("Content-Type", "")
            mime_type = self._normalize_mime_type(content_type)
            if not mime_type:
                mime_type = self._guess_mime_type_from_url(url)
            if not mime_type:
                return None
            encoded = base64.b64encode(payload).decode("ascii")
            return f"data:{mime_type};base64,{encoded}"
        except (TimeoutError, OSError, URLError, ValueError):
            return None

    def _normalize_mime_type(self, content_type_header: str) -> str:
        if not content_type_header:
            return ""
        mime = content_type_header.split(";", 1)[0].strip().lower()
        return mime if mime.startswith("image/") else ""

    def _guess_mime_type_from_url(self, url: str) -> str:
        path = urlsplit(url).path.lower()
        if path.endswith(".png"):
            return "image/png"
        if path.endswith(".jpg") or path.endswith(".jpeg"):
            return "image/jpeg"
        if path.endswith(".gif"):
            return "image/gif"
        if path.endswith(".webp"):
            return "image/webp"
        if path.endswith(".svg"):
            return "image/svg+xml"
        return ""


class YahooMailService:
    def __init__(
        self,
        settings_store: SettingsStore,
        action_logger: ActionLogger,
        ai_client: AIClient,
        imap_factory: Callable[[str, int], imaplib.IMAP4_SSL] | None = None,
        smtp_factory: Callable[[str, int], smtplib.SMTP_SSL] | None = None,
        remote_url_opener: Callable[[Request, float], object] | None = None,
    ) -> None:
        self._settings_store = settings_store
        self._action_logger = action_logger
        self._ai_client = ai_client
        self._imap_factory = imap_factory or imaplib.IMAP4_SSL
        self._smtp_factory = smtp_factory or smtplib.SMTP_SSL
        self._remote_url_opener = remote_url_opener or urlopen

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

    def summarize_email(self, uid: str, on_status=None, on_partial=None, is_cancelled=None) -> str:
        message = self.read_email(uid)
        settings = self._settings_store.load()
        try:
            return self._ai_client.generate_final_text(
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
                on_status=on_status,
                on_partial=on_partial,
                is_cancelled=is_cancelled,
            )
        except (AIUnavailableError, AIClientError) as exc:
            self._record("email_summarize", uid, "error", str(exc))
            raise YahooMailError(str(exc)) from exc

    def draft_reply(self, uid: str, user_notes: str = "", on_status=None, on_partial=None, is_cancelled=None) -> OutgoingDraft:
        message = self.read_email(uid)
        settings = self._settings_store.load()
        try:
            draft_text = self._ai_client.generate_final_text(
                settings,
                system_prompt=(
                    "You draft clear, polite email replies for a non-technical user. "
                    "Return only the final email body text that can be pasted directly into a draft."
                ),
                user_prompt=(
                    "Draft a reply email body.\n"
                    "- Output final answer text only.\n"
                    "- No chain-of-thought, analysis, preamble, JSON, markdown, or code fences.\n"
                    "- Keep it practical and ready to send.\n"
                    "- Do not invent facts that are not in the original message or user notes.\n\n"
                    f"Original sender: {message.sender}\n"
                    f"Original subject: {message.subject}\n"
                    f"Original body:\n{message.body_text[:8000]}\n\n"
                    f"User notes for the reply:\n{user_notes or 'No extra notes provided.'}"
                ),
                on_status=on_status,
                on_partial=on_partial,
                is_cancelled=is_cancelled,
            )
            draft_text = self._normalize_draft_text(draft_text)
        except (AIUnavailableError, AIClientError) as exc:
            self._record("email_draft_reply", uid, "error", str(exc))
            raise YahooMailError(self._humanize_draft_error(exc)) from exc
        reply_subject = message.subject if message.subject.lower().startswith("re:") else f"Re: {message.subject}"
        self._record("email_draft_reply", uid, "success")
        return OutgoingDraft(
            to_address=self._extract_address(message.sender),
            subject=reply_subject,
            body=draft_text,
        )

    def draft_new_email(self, to_address: str, subject: str, user_notes: str, on_status=None, on_partial=None, is_cancelled=None) -> OutgoingDraft:
        settings = self._settings_store.load()
        try:
            draft_text = self._ai_client.generate_final_text(
                settings,
                system_prompt=(
                    "You draft clear, friendly outbound emails for a non-technical user. "
                    "Return only directly-usable final email body text."
                ),
                user_prompt=(
                    "Write a new email draft body.\n"
                    "- Output final answer text only.\n"
                    "- No chain-of-thought, analysis, preamble, JSON, markdown, or code fences.\n"
                    "- Keep it clear, specific, and not overly formal.\n\n"
                    f"To: {to_address or 'Not specified yet'}\n"
                    f"Subject: {subject or 'Not specified yet'}\n"
                    f"What the user wants to say:\n{(user_notes or 'No notes provided.')[:4000]}"
                ),
                on_status=on_status,
                on_partial=on_partial,
                is_cancelled=is_cancelled,
            )
            draft_text = self._normalize_draft_text(draft_text)
        except (AIUnavailableError, AIClientError) as exc:
            self._record("email_draft_new", to_address or subject or "new", "error", str(exc))
            raise YahooMailError(self._humanize_draft_error(exc)) from exc
        self._record("email_draft_new", to_address or subject or "new", "success")
        return OutgoingDraft(to_address=to_address.strip(), subject=subject.strip(), body=draft_text)

    def _normalize_draft_text(self, text: str) -> str:
        normalized = text.strip()
        if not normalized:
            raise AIModelOutputError(
                reason="empty_output",
                message="The model returned an empty or whitespace-only final draft body.",
            )
        if normalized.startswith("```") or normalized.endswith("```"):
            raise AIModelOutputError(
                reason="malformed_content",
                message="The model returned markdown fencing instead of directly-usable email body text.",
            )
        lowered = normalized.lower()
        if lowered.startswith("{") and lowered.endswith("}"):
            raise AIModelOutputError(
                reason="malformed_content",
                message="The model returned JSON instead of directly-usable email body text.",
            )
        return normalized

    def _humanize_draft_error(self, exc: Exception) -> str:
        if isinstance(exc, AITimeoutError):
            return "Draft generation timed out before the model produced a usable final draft body."
        if isinstance(exc, AIModelOutputError):
            if exc.reason == "no_stream":
                return "Draft generation failed because the local model returned no stream output."
            if exc.reason == "reasoning_only_stream":
                return "Draft generation failed because the local model produced reasoning only and no final draft body."
            if exc.reason == "empty_output":
                return "Draft generation failed because the model returned an empty final draft body."
            if exc.reason == "malformed_content":
                return "Draft generation failed because the model output was malformed for an email draft body."
        return str(exc)

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

    def build_safe_preview_html(self, message: MailMessageView, allow_remote_images: bool) -> str:
        if message.body_html.strip():
            loader = _RemoteImageLoader(self._remote_url_opener) if allow_remote_images else None
            sanitizer = _SafeHTMLSanitizer(allow_remote_images=allow_remote_images, remote_image_loader=loader)
            sanitizer.feed(message.body_html)
            rendered_body = sanitizer.html().strip()
            if not rendered_body:
                rendered_body = "<p>(HTML body became empty after safety filtering.)</p>"
        else:
            rendered_body = f"<pre>{self._escape_html(message.body_text)}</pre>"

        remote_note = ""
        if message.body_html and not allow_remote_images:
            remote_note = (
                "<p><i>Remote images are blocked for privacy. "
                "Use 'Load remote images' to enable them for this message.</i></p>"
            )

        return (
            "<html><body>"
            f"<h3>{self._escape_html(message.subject)}</h3>"
            f"<p><b>From:</b> {self._escape_html(message.sender)}<br>"
            f"<b>To:</b> {self._escape_html(message.recipients)}<br>"
            f"<b>Date:</b> {self._escape_html(message.received_at)}</p>"
            f"{remote_note}"
            "<hr>"
            f"{rendered_body}"
            "</body></html>"
        )

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
        body_text, body_html, inline_images, attachments = self._extract_bodies_and_metadata(parsed)
        return MailMessageView(
            uid=uid,
            subject=self._decode_header_value(parsed.get("Subject", "(No subject)")),
            sender=self._decode_header_value(parsed.get("From", "(Unknown sender)")),
            recipients=self._decode_header_value(parsed.get("To", "")),
            received_at=self._safe_date(parsed.get("Date", "")),
            unread=b"\\Seen" not in flag_bytes,
            body_text=body_text,
            body_html=body_html,
            inline_images=inline_images,
            attachments=attachments,
        )

    def _extract_bodies_and_metadata(
        self,
        message: Message,
    ) -> tuple[str, str, list[InlineImageMeta], list[AttachmentMeta]]:
        plain_parts: list[str] = []
        html_parts: list[str] = []
        inline_images: list[InlineImageMeta] = []
        attachments: list[AttachmentMeta] = []
        inline_cid_to_data_url: dict[str, str] = {}

        for part in message.walk():
            if part.is_multipart():
                continue
            content_type = part.get_content_type().lower()
            disposition = (part.get_content_disposition() or "").lower()
            payload = part.get_payload(decode=True)
            filename = self._decode_header_value(part.get_filename() or "")
            size = len(payload) if payload else 0
            content_id = self._clean_content_id(part.get("Content-ID", ""))

            if content_type == "text/plain" and disposition != "attachment":
                plain_parts.append(self._decode_text_payload(part, payload))
                continue

            if content_type == "text/html" and disposition != "attachment":
                html_parts.append(self._decode_text_payload(part, payload))
                continue

            if content_type.startswith("image/") and disposition in {"inline", ""} and content_id and payload:
                data_url = self._to_data_url(content_type, payload)
                inline_cid_to_data_url[content_id] = data_url
                inline_images.append(
                    InlineImageMeta(
                        content_id=content_id,
                        content_type=content_type,
                        filename=filename,
                        size=size,
                    )
                )
                continue

            if disposition == "attachment" or filename:
                attachments.append(
                    AttachmentMeta(
                        filename=filename or "(unnamed attachment)",
                        content_type=content_type,
                        size=size,
                        disposition=disposition or "attachment",
                    )
                )

        body_text = "\n\n".join(self._normalize_text(part) for part in plain_parts if part.strip()).strip()
        body_html = "\n<hr>\n".join(part.strip() for part in html_parts if part.strip()).strip()
        if body_html:
            body_html = self._replace_cid_sources(body_html, inline_cid_to_data_url)
        elif body_text:
            body_html = f"<pre>{self._escape_html(body_text)}</pre>"

        if not body_text and body_html:
            extractor = _HTMLTextExtractor()
            extractor.feed(body_html)
            body_text = self._normalize_text(extractor.text())

        return body_text, body_html, inline_images, attachments

    def _decode_text_payload(self, part: Message, payload: bytes | None) -> str:
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        return payload.decode(charset, errors="replace")

    def _replace_cid_sources(self, html: str, cid_map: dict[str, str]) -> str:
        def _repl(match: re.Match[str]) -> str:
            quote = match.group("quote")
            cid_value = self._clean_content_id(match.group("cid"))
            replacement = cid_map.get(cid_value)
            if not replacement:
                return match.group(0)
            return f'src={quote}{replacement}{quote}'

        pattern = re.compile(r"src\s*=\s*(?P<quote>['\"])cid:(?P<cid>[^'\"]+)(?P=quote)", re.IGNORECASE)
        return pattern.sub(_repl, html)

    def _clean_content_id(self, value: str) -> str:
        return value.strip().strip("<>").strip()

    def _to_data_url(self, content_type: str, payload: bytes) -> str:
        encoded = base64.b64encode(payload).decode("ascii")
        return f"data:{content_type};base64,{encoded}"

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

    def _escape_html(self, value: str) -> str:
        return (
            value.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;")
        )
