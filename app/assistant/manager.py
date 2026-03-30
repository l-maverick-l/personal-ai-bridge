from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum

from app.ai.client import AIClient, AIClientError, AIUnavailableError
from app.email.yahoo_service import MailSummary, OutgoingDraft, YahooMailError, YahooMailService
from app.files.service import FileOperationError, FileService
from app.models.settings import AppSettings


class AssistantIntent(str, Enum):
    ASK_ONLY = "ask-only"
    FILE_READ_SEARCH = "file read/search"
    FILE_ACTION = "file action"
    EMAIL_READ_SEARCH = "email read/search"
    EMAIL_DRAFT_SEND = "email draft/send"
    MIXED_REQUEST = "mixed request"


@dataclass(slots=True)
class AssistantContext:
    selected_root: str = ""
    selected_file_path: str = ""
    open_folder_path: str = ""
    selected_email_uid: str = ""
    selected_email_subject: str = ""


@dataclass(slots=True)
class AssistantActionProposal:
    action_type: str
    title: str
    parameters: dict[str, str] = field(default_factory=dict)
    requires_confirmation: bool = False


@dataclass(slots=True)
class AssistantResponse:
    intent: AssistantIntent
    answer_text: str
    proposed_actions: list[AssistantActionProposal] = field(default_factory=list)
    used_context: list[str] = field(default_factory=list)


class AssistantService:
    def __init__(self, file_service: FileService, yahoo_service: YahooMailService, ai_client: AIClient) -> None:
        self._file_service = file_service
        self._yahoo_service = yahoo_service
        self._ai_client = ai_client

    def classify_intent(self, request_text: str) -> AssistantIntent:
        lowered = request_text.lower()
        has_file = any(word in lowered for word in ["file", "folder", "download", "path"])
        has_email = any(word in lowered for word in ["email", "mail", "inbox", "reply", "draft"])
        if has_file and has_email:
            return AssistantIntent.MIXED_REQUEST
        if any(word in lowered for word in ["move", "delete", "rename", "copy", "create"]):
            return AssistantIntent.FILE_ACTION if has_file or "selected" in lowered else AssistantIntent.MIXED_REQUEST
        if any(word in lowered for word in ["draft", "reply", "send"]) and has_email:
            return AssistantIntent.EMAIL_DRAFT_SEND
        if has_email:
            return AssistantIntent.EMAIL_READ_SEARCH
        if has_file or any(word in lowered for word in ["summarize", "summary", "find", "search", "read"]):
            return AssistantIntent.FILE_READ_SEARCH
        return AssistantIntent.ASK_ONLY

    def handle_request(self, request_text: str, context: AssistantContext, settings: AppSettings) -> AssistantResponse:
        intent = self.classify_intent(request_text)
        lowered = request_text.lower().strip()
        if intent == AssistantIntent.FILE_ACTION:
            return self._handle_file_action(request_text, lowered, context, intent)
        if intent == AssistantIntent.EMAIL_DRAFT_SEND:
            return self._handle_email_draft(request_text, lowered, context, settings, intent)
        if intent == AssistantIntent.FILE_READ_SEARCH:
            return self._handle_file_read_search(request_text, lowered, context, settings, intent)
        if intent == AssistantIntent.EMAIL_READ_SEARCH:
            return self._handle_email_read_search(request_text, lowered, context, settings, intent)
        if intent == AssistantIntent.MIXED_REQUEST:
            return AssistantResponse(
                intent=intent,
                answer_text=(
                    "I detected both file and email tasks in one request. Please split it into two short requests "
                    "(one for files, one for email) so I can execute safely."
                ),
            )
        return self._handle_ask_only(request_text, context, settings, intent)

    def _handle_ask_only(
        self,
        request_text: str,
        context: AssistantContext,
        settings: AppSettings,
        intent: AssistantIntent,
    ) -> AssistantResponse:
        used_context = self._context_labels(context)
        prompt = (
            "Answer the user in plain English in 3-6 short lines. "
            "If context details are provided, mention how they affect your answer.\n\n"
            f"Context: {', '.join(used_context) if used_context else 'none'}\n"
            f"User request: {request_text}"
        )
        try:
            answer = self._ai_client.generate_text(
                settings=settings,
                system_prompt="You are a safe desktop assistant for files and email.",
                user_prompt=prompt,
            )
        except (AIUnavailableError, AIClientError):
            answer = "AI is not configured right now. I can still run direct file and email operations from specific requests."
        return AssistantResponse(intent=intent, answer_text=answer.strip(), used_context=used_context)

    def _handle_file_read_search(
        self,
        request_text: str,
        lowered: str,
        context: AssistantContext,
        settings: AppSettings,
        intent: AssistantIntent,
    ) -> AssistantResponse:
        used_context = self._context_labels(context)
        root = context.selected_root
        if not root:
            return AssistantResponse(intent=intent, answer_text="Select an approved root folder first.", used_context=used_context)

        if "summarize" in lowered and context.selected_file_path:
            try:
                summary = self._file_service.summarize_file(root, context.selected_file_path)
                return AssistantResponse(
                    intent=intent,
                    answer_text=f"Summary for {context.selected_file_path}:\n\n{summary}",
                    used_context=used_context + ["selected file"],
                )
            except FileOperationError as exc:
                return AssistantResponse(intent=intent, answer_text=str(exc), used_context=used_context)

        if "read" in lowered and context.selected_file_path:
            try:
                read_result = self._file_service.read_file(root, context.selected_file_path)
                preview = read_result.content[:3000]
                suffix = "\n\n(Preview truncated.)" if len(read_result.content) > 3000 else ""
                return AssistantResponse(
                    intent=intent,
                    answer_text=f"Read {context.selected_file_path}:\n\n{preview}{suffix}",
                    used_context=used_context + ["selected file"],
                )
            except FileOperationError as exc:
                return AssistantResponse(intent=intent, answer_text=str(exc), used_context=used_context)

        query = self._extract_search_query(request_text)
        if query:
            try:
                matches = self._file_service.search_files(root, query)
                if not matches:
                    return AssistantResponse(intent=intent, answer_text=f"I found no files matching '{query}'.", used_context=used_context)
                top = "\n".join(f"- {item.relative_path}" for item in matches[:20])
                return AssistantResponse(
                    intent=intent,
                    answer_text=f"I found {len(matches)} match(es) for '{query}':\n{top}",
                    used_context=used_context,
                )
            except FileOperationError as exc:
                return AssistantResponse(intent=intent, answer_text=str(exc), used_context=used_context)

        if context.open_folder_path:
            try:
                listing = self._file_service.list_directory(root, context.open_folder_path)
                top = "\n".join(f"- {entry.relative_path}" for entry in listing.entries[:20]) or "(empty folder)"
                return AssistantResponse(
                    intent=intent,
                    answer_text=f"Folder {context.open_folder_path or '.'} has {len(listing.entries)} item(s):\n{top}",
                    used_context=used_context + ["open folder"],
                )
            except FileOperationError as exc:
                return AssistantResponse(intent=intent, answer_text=str(exc), used_context=used_context)

        return AssistantResponse(intent=intent, answer_text="Tell me what file to read/summarize, or provide a search phrase.", used_context=used_context)

    def _handle_file_action(
        self,
        request_text: str,
        lowered: str,
        context: AssistantContext,
        intent: AssistantIntent,
    ) -> AssistantResponse:
        used_context = self._context_labels(context)
        root = context.selected_root
        if not root:
            return AssistantResponse(intent=intent, answer_text="Select an approved root folder first.", used_context=used_context)
        if "delete" in lowered:
            if not context.selected_file_path:
                return AssistantResponse(intent=intent, answer_text="Select a file first, then ask to delete it.", used_context=used_context)
            proposal = AssistantActionProposal(
                action_type="file_delete",
                title=f"Delete file {context.selected_file_path}",
                parameters={"root": root, "relative_path": context.selected_file_path},
                requires_confirmation=True,
            )
            return AssistantResponse(
                intent=intent,
                answer_text="I prepared a delete action proposal. It will require confirmation before execution.",
                proposed_actions=[proposal],
                used_context=used_context + ["selected file"],
            )
        if "move" in lowered:
            if not context.selected_file_path:
                return AssistantResponse(intent=intent, answer_text="Select a file first, then ask where to move it.", used_context=used_context)
            destination = self._extract_destination_path(request_text)
            if not destination:
                return AssistantResponse(intent=intent, answer_text="I could not detect the destination path. Example: 'Move this file to Taxes/2026/receipt.txt'.", used_context=used_context)
            proposal = AssistantActionProposal(
                action_type="file_move",
                title=f"Move file to {destination}",
                parameters={
                    "source_root": root,
                    "source_relative_path": context.selected_file_path,
                    "destination_root": root,
                    "destination_relative_path": destination,
                },
                requires_confirmation=True,
            )
            return AssistantResponse(
                intent=intent,
                answer_text="I prepared a move action proposal. It will require confirmation before execution.",
                proposed_actions=[proposal],
                used_context=used_context + ["selected file"],
            )
        return AssistantResponse(
            intent=intent,
            answer_text="I can currently prepare file action proposals for move and delete requests.",
            used_context=used_context,
        )

    def _handle_email_read_search(
        self,
        request_text: str,
        lowered: str,
        context: AssistantContext,
        settings: AppSettings,
        intent: AssistantIntent,
    ) -> AssistantResponse:
        used_context = self._context_labels(context)
        if "what does this email" in lowered or ("summarize" in lowered and context.selected_email_uid):
            if not context.selected_email_uid:
                return AssistantResponse(intent=intent, answer_text="Select an email first.", used_context=used_context)
            try:
                summary = self._yahoo_service.summarize_email(context.selected_email_uid)
                return AssistantResponse(
                    intent=intent,
                    answer_text=f"Here is what the selected email wants:\n\n{summary}",
                    used_context=used_context + ["selected email"],
                )
            except YahooMailError as exc:
                return AssistantResponse(intent=intent, answer_text=str(exc), used_context=used_context)

        query = self._extract_search_query(request_text)
        try:
            messages = self._yahoo_service.list_inbox(subject_keyword=query if query else "")
            if not messages:
                return AssistantResponse(intent=intent, answer_text="I found no matching emails.", used_context=used_context)
            bullets = self._render_mail_list(messages)
            return AssistantResponse(intent=intent, answer_text=f"Top Yahoo results:\n{bullets}", used_context=used_context)
        except YahooMailError as exc:
            return AssistantResponse(intent=intent, answer_text=str(exc), used_context=used_context)

    def _handle_email_draft(
        self,
        request_text: str,
        lowered: str,
        context: AssistantContext,
        settings: AppSettings,
        intent: AssistantIntent,
    ) -> AssistantResponse:
        used_context = self._context_labels(context)
        if "send" in lowered:
            proposal = AssistantActionProposal(
                action_type="email_send",
                title="Send current draft email",
                parameters={},
                requires_confirmation=True,
            )
            return AssistantResponse(
                intent=intent,
                answer_text="I prepared a send proposal. Sending always requires explicit confirmation.",
                proposed_actions=[proposal],
                used_context=used_context,
            )

        if "reply" in lowered and context.selected_email_uid:
            note = self._extract_after_keyword(request_text, "saying") or request_text
            try:
                draft = self._yahoo_service.draft_reply(context.selected_email_uid, note)
                proposal = AssistantActionProposal(
                    action_type="email_use_draft",
                    title="Apply drafted reply to editor",
                    parameters={"to": draft.to_address, "subject": draft.subject, "body": draft.body},
                    requires_confirmation=False,
                )
                return AssistantResponse(
                    intent=intent,
                    answer_text="I drafted a reply. Review it before sending.",
                    proposed_actions=[proposal],
                    used_context=used_context + ["selected email"],
                )
            except YahooMailError as exc:
                return AssistantResponse(intent=intent, answer_text=str(exc), used_context=used_context)

        to_address, subject = self._extract_email_targets(request_text)
        note = self._extract_after_keyword(request_text, "saying") or request_text
        try:
            draft = self._yahoo_service.draft_new_email(to_address, subject, note)
            proposal = AssistantActionProposal(
                action_type="email_use_draft",
                title="Apply drafted email to editor",
                parameters={"to": draft.to_address, "subject": draft.subject, "body": draft.body},
                requires_confirmation=False,
            )
            return AssistantResponse(
                intent=intent,
                answer_text="I drafted a new email. Review it before sending.",
                proposed_actions=[proposal],
                used_context=used_context,
            )
        except YahooMailError as exc:
            return AssistantResponse(intent=intent, answer_text=str(exc), used_context=used_context)

    def _extract_search_query(self, request_text: str) -> str:
        matched = re.search(r"(?:mention|mentions|for|about)\s+(.+)$", request_text, flags=re.IGNORECASE)
        if matched:
            return matched.group(1).strip(" .?!\"")
        quoted = re.search(r'"([^"]+)"', request_text)
        if quoted:
            return quoted.group(1).strip()
        return ""

    def _extract_destination_path(self, request_text: str) -> str:
        matched = re.search(r"\bto\b\s+(.+)$", request_text, flags=re.IGNORECASE)
        if not matched:
            return ""
        return matched.group(1).strip(" .?!\"")

    def _extract_after_keyword(self, request_text: str, keyword: str) -> str:
        matched = re.search(rf"\b{re.escape(keyword)}\b\s+(.+)$", request_text, flags=re.IGNORECASE)
        return matched.group(1).strip() if matched else ""

    def _extract_email_targets(self, request_text: str) -> tuple[str, str]:
        mail = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", request_text)
        subject = ""
        subject_match = re.search(r"subject\s*[:=]\s*([^\n]+)", request_text, flags=re.IGNORECASE)
        if subject_match:
            subject = subject_match.group(1).strip()
        return (mail.group(0) if mail else "", subject)

    def _render_mail_list(self, messages: list[MailSummary]) -> str:
        return "\n".join(f"- {m.received_at} | {m.sender} | {m.subject}" for m in messages[:12])

    def _context_labels(self, context: AssistantContext) -> list[str]:
        labels: list[str] = []
        if context.selected_file_path:
            labels.append(f"selected file {context.selected_file_path}")
        if context.open_folder_path:
            labels.append(f"open folder {context.open_folder_path}")
        if context.selected_email_subject:
            labels.append(f"selected email '{context.selected_email_subject}'")
        return labels


def proposal_to_json(proposal: AssistantActionProposal) -> str:
    return json.dumps(
        {
            "action_type": proposal.action_type,
            "title": proposal.title,
            "parameters": proposal.parameters,
            "requires_confirmation": proposal.requires_confirmation,
        },
        indent=2,
    )
