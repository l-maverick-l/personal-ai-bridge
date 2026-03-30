from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from collections.abc import Callable
from typing import Any

from app.ai.client import AIClient, AIClientError, AIModelOutputError, AIUnavailableError
from app.email.yahoo_service import OutgoingDraft, YahooMailError, YahooMailService
from app.files.service import FileOperationError, FileService
from app.models.settings import AppSettings


class AssistantIntent(str, Enum):
    AGENT = "agent"


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


@dataclass(slots=True)
class _AgentStep:
    tool_name: str
    args: dict[str, Any]
    result: dict[str, Any]


class AssistantService:
    _MAX_AGENT_STEPS = 4
    _SAFE_TOOLS: tuple[str, ...] = (
        "list_directory",
        "search_files",
        "read_file",
        "summarize_file",
        "create_file",
        "rename_file",
        "copy_file",
        "move_file",
        "delete_file",
        "list_inbox",
        "read_email",
        "summarize_email",
        "draft_reply",
        "draft_new_email",
        "send_email",
    )
    _REQUIRED_PLAN_FIELDS: tuple[str, ...] = (
        "intent",
        "tool_calls",
        "final_answer",
        "proposed_actions",
        "needs_confirmation",
    )

    def __init__(self, file_service: FileService, yahoo_service: YahooMailService, ai_client: AIClient) -> None:
        self._file_service = file_service
        self._yahoo_service = yahoo_service
        self._ai_client = ai_client

    def handle_request(
        self,
        request_text: str,
        context: AssistantContext,
        settings: AppSettings,
        on_status: Callable[[str], None] | None = None,
    ) -> AssistantResponse:
        emit_status = on_status or (lambda _text: None)
        emit_status("analyzing request")
        used_context = self._context_labels(context)
        steps: list[_AgentStep] = []
        proposals: list[AssistantActionProposal] = []

        for index in range(self._MAX_AGENT_STEPS):
            emit_status("planning tools")
            model_plan = self._plan_with_ai(request_text, context, settings, steps, index + 1)
            tool_calls = model_plan.get("tool_calls") if isinstance(model_plan.get("tool_calls"), list) else []
            final_answer = str(model_plan.get("final_answer", "")).strip()

            if tool_calls:
                for call in tool_calls:
                    emit_status("running tool call")
                    execution = self._execute_tool_call(call, context)
                    if execution.get("proposal"):
                        proposals.append(execution["proposal"])
                    serializable_result = dict(execution)
                    if "proposal" in serializable_result:
                        proposal = serializable_result.pop("proposal")
                        if isinstance(proposal, AssistantActionProposal):
                            serializable_result["proposal_meta"] = {
                                "action_type": proposal.action_type,
                                "title": proposal.title,
                                "requires_confirmation": proposal.requires_confirmation,
                            }
                    steps.append(
                        _AgentStep(
                            tool_name=str(serializable_result.get("tool_name", "unknown")),
                            args=serializable_result.get("args", {}),
                            result=serializable_result,
                        )
                    )
                continue

            if final_answer:
                emit_status("generating final answer")
                return AssistantResponse(
                    intent=AssistantIntent.AGENT,
                    answer_text=final_answer,
                    proposed_actions=proposals,
                    used_context=used_context,
                )

            return AssistantResponse(
                intent=AssistantIntent.AGENT,
                answer_text="I could not complete this request because the AI response was missing both tool calls and a final answer.",
                proposed_actions=proposals,
                used_context=used_context,
            )

        return AssistantResponse(
            intent=AssistantIntent.AGENT,
            answer_text=(
                "I stopped after several tool steps to stay safe. Please retry with a narrower request, "
                "or run the action manually in the Files or Yahoo Mail tabs."
            ),
            proposed_actions=proposals,
            used_context=used_context,
        )

    def _plan_with_ai(
        self,
        request_text: str,
        context: AssistantContext,
        settings: AppSettings,
        steps: list[_AgentStep],
        step_number: int,
    ) -> dict[str, Any]:
        payload = {
            "request": request_text,
            "context": {
                "selected_approved_root": context.selected_root,
                "current_folder_path": context.open_folder_path,
                "current_folder_is_root": context.open_folder_path in ("", "."),
                "selected_file_path": context.selected_file_path,
                "selected_email_uid": context.selected_email_uid,
                "selected_email_subject": context.selected_email_subject,
                "available_tools": list(self._SAFE_TOOLS),
            },
            "step_number": step_number,
            "prior_tool_results": [
                {
                    "tool": step.tool_name,
                    "args": step.args,
                    "result": step.result,
                }
                for step in steps
            ],
        }

        system_prompt = (
            "You are a safe desktop assistant. Use tools for file and Yahoo mail tasks. "
            "Never request shell commands. Return strict JSON only."
        )
        user_prompt = (
            "Return only JSON with this schema: "
            '{"intent":"string","tool_calls":[{"name":"tool_name","arguments":{}}],'
            '"final_answer":"plain english string","proposed_actions":[],"needs_confirmation":bool}. '
            "Rules: If tools are needed, fill tool_calls and leave final_answer empty. "
            "If ready to answer user, set final_answer and tool_calls=[]. "
            "For destructive/external actions (delete/move/overwrite/send_email), still use tool_calls.\n\n"
            f"Input JSON:\n{json.dumps(payload, indent=2)}"
        )

        try:
            raw = self._ai_client.generate_structured_json(
                settings=settings,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
            )
        except (AIUnavailableError, AIClientError) as exc:
            return {
                "intent": "error",
                "tool_calls": [],
                "final_answer": self._planner_error_from_exception(exc),
                "proposed_actions": [],
                "needs_confirmation": False,
            }

        parsed, parse_error = self._parse_structured_output(raw)
        if parsed:
            return parsed

        repair_prompt = (
            "Your previous response was invalid. Repair it. Return strict JSON only with keys: "
            'intent, tool_calls, final_answer, proposed_actions, needs_confirmation. '
            "Do not add markdown fences.\n\n"
            f"Invalid response:\n{raw}"
        )
        try:
            repaired = self._ai_client.generate_structured_json(
                settings=settings,
                system_prompt="Return strict JSON only.",
                user_prompt=repair_prompt,
            )
        except (AIUnavailableError, AIClientError) as exc:
            return {
                "intent": "error",
                "tool_calls": [],
                "final_answer": (
                    f"{self._planner_error_from_parse(parse_error)} "
                    f"Repair attempt also failed: {self._planner_error_from_exception(exc)}"
                ),
                "proposed_actions": [],
                "needs_confirmation": False,
            }

        repaired_parsed, repaired_error = self._parse_structured_output(repaired)
        if repaired_parsed:
            return repaired_parsed
        return {
            "intent": "error",
            "tool_calls": [],
            "final_answer": f"{self._planner_error_from_parse(repaired_error)} Try a stronger local model for agent planning.",
            "proposed_actions": [],
            "needs_confirmation": False,
        }

    def _parse_structured_output(self, raw_text: str) -> tuple[dict[str, Any] | None, str]:
        text = raw_text.strip()
        if not text:
            return None, "empty_output"
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE | re.DOTALL).strip()
        if not text:
            return None, "whitespace_output"
        try:
            candidate = json.loads(text)
        except json.JSONDecodeError:
            candidate = self._try_parse_first_json_object(text)
        if candidate is None:
            return None, "malformed_json"
        if not isinstance(candidate, dict):
            return None, "malformed_json"

        missing_fields = [field for field in self._REQUIRED_PLAN_FIELDS if field not in candidate]
        if missing_fields:
            return None, f"missing_fields:{','.join(missing_fields)}"

        tool_calls = candidate.get("tool_calls", [])
        if tool_calls is None:
            tool_calls = []
        if not isinstance(tool_calls, list):
            return None, "invalid_tool_calls"

        normalized = {
            "intent": str(candidate.get("intent", "general")),
            "tool_calls": tool_calls,
            "final_answer": str(candidate.get("final_answer", "")).strip(),
            "proposed_actions": candidate.get("proposed_actions", []),
            "needs_confirmation": bool(candidate.get("needs_confirmation", False)),
        }
        return normalized, ""

    def _planner_error_from_exception(self, exc: Exception) -> str:
        if isinstance(exc, AIModelOutputError):
            if exc.reason == "no_stream":
                return "The local model returned no stream output for planning."
            if exc.reason == "reasoning_only_stream":
                return "The local model streamed reasoning/thinking output only and never emitted a final planner answer."
            if exc.reason == "no_final_answer":
                return "The local model streamed output but never emitted a usable final planner answer."
            if exc.reason == "whitespace_only":
                return "The local model returned whitespace-only output."
        return f"AI planning failed: {exc}"

    def _planner_error_from_parse(self, parse_error: str) -> str:
        if parse_error == "empty_output":
            return "The planner output was empty."
        if parse_error == "whitespace_output":
            return "The planner output was whitespace only."
        if parse_error == "malformed_json":
            return "The model could not follow the required JSON format."
        if parse_error.startswith("missing_fields:"):
            missing = parse_error.split(":", 1)[1]
            return f"The model returned JSON but missed required planner fields: {missing}."
        if parse_error == "invalid_tool_calls":
            return "The model returned JSON but tool_calls was not a list."
        return "The model output could not be parsed into the planner schema."

    def _try_parse_first_json_object(self, text: str) -> dict[str, Any] | None:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        snippet = text[start : end + 1]
        try:
            parsed = json.loads(snippet)
        except json.JSONDecodeError:
            return None
        return parsed if isinstance(parsed, dict) else None

    def _execute_tool_call(self, tool_call: Any, context: AssistantContext) -> dict[str, Any]:
        if not isinstance(tool_call, dict):
            return {"ok": False, "error": "Tool call must be an object.", "tool_name": "unknown", "args": {}}
        tool_name = str(tool_call.get("name", "")).strip()
        args = tool_call.get("arguments")
        if not isinstance(args, dict):
            args = {}
        if tool_name not in self._SAFE_TOOLS:
            return {"ok": False, "error": f"Tool not allowed: {tool_name}", "tool_name": tool_name, "args": args}

        try:
            if tool_name == "list_directory":
                root = self._required_root(context, args)
                relative_path = str(args.get("relative_path") or context.open_folder_path or "")
                listing = self._file_service.list_directory(root, relative_path)
                return {
                    "ok": True,
                    "tool_name": tool_name,
                    "args": args,
                    "result": {
                        "root": listing.root,
                        "relative_path": listing.relative_path,
                        "entries": [
                            {"relative_path": item.relative_path, "is_dir": item.is_dir, "size": item.size}
                            for item in listing.entries[:100]
                        ],
                    },
                }
            if tool_name == "search_files":
                root = self._required_root(context, args)
                query = str(args.get("query", "")).strip()
                matches = self._file_service.search_files(root, query)
                return {
                    "ok": True,
                    "tool_name": tool_name,
                    "args": args,
                    "result": {
                        "query": query,
                        "count": len(matches),
                        "matches": [item.relative_path for item in matches[:100]],
                    },
                }
            if tool_name == "read_file":
                root = self._required_root(context, args)
                relative_path = self._required_relative_path(context, args, "relative_path", "selected_file_path")
                data = self._file_service.read_file(root, relative_path)
                return {
                    "ok": True,
                    "tool_name": tool_name,
                    "args": args,
                    "result": {"relative_path": data.relative_path, "content": data.content[:12000]},
                }
            if tool_name == "summarize_file":
                root = self._required_root(context, args)
                relative_path = self._required_relative_path(context, args, "relative_path", "selected_file_path")
                summary = self._file_service.summarize_file(root, relative_path)
                return {
                    "ok": True,
                    "tool_name": tool_name,
                    "args": args,
                    "result": {"relative_path": relative_path, "summary": summary},
                }
            if tool_name == "create_file":
                root = self._required_root(context, args)
                relative_path = str(args.get("relative_path", "")).strip()
                content = str(args.get("content", ""))
                created_path = self._file_service.create_file(root, relative_path, content)
                return {"ok": True, "tool_name": tool_name, "args": args, "result": {"created_path": created_path}}
            if tool_name == "rename_file":
                root = self._required_root(context, args)
                relative_path = self._required_relative_path(context, args, "relative_path", "selected_file_path")
                new_name = str(args.get("new_name", "")).strip()
                renamed_path = self._file_service.rename_file(root, relative_path, new_name)
                return {"ok": True, "tool_name": tool_name, "args": args, "result": {"renamed_path": renamed_path}}
            if tool_name == "copy_file":
                source_root = str(args.get("source_root") or context.selected_root).strip()
                source_relative = self._required_relative_path(context, args, "source_relative_path", "selected_file_path")
                destination_root = str(args.get("destination_root") or source_root).strip()
                destination_relative = str(args.get("destination_relative_path", "")).strip()
                overwrite = bool(args.get("overwrite", False))
                if overwrite:
                    return {
                        "ok": True,
                        "tool_name": tool_name,
                        "args": args,
                        "proposal": AssistantActionProposal(
                            action_type="file_copy",
                            title=f"Copy file to {destination_relative} (overwrite)",
                            parameters={
                                "source_root": source_root,
                                "source_relative_path": source_relative,
                                "destination_root": destination_root,
                                "destination_relative_path": destination_relative,
                                "overwrite": "true",
                            },
                            requires_confirmation=True,
                        ),
                        "result": {"status": "confirmation_required"},
                    }
                copied_path = self._file_service.copy_file(source_root, source_relative, destination_root, destination_relative, overwrite=False)
                return {"ok": True, "tool_name": tool_name, "args": args, "result": {"copied_path": copied_path}}
            if tool_name == "move_file":
                source_root = str(args.get("source_root") or context.selected_root).strip()
                source_relative = self._required_relative_path(context, args, "source_relative_path", "selected_file_path")
                destination_root = str(args.get("destination_root") or source_root).strip()
                destination_relative = str(args.get("destination_relative_path", "")).strip()
                proposal = AssistantActionProposal(
                    action_type="file_move",
                    title=f"Move file to {destination_relative}",
                    parameters={
                        "source_root": source_root,
                        "source_relative_path": source_relative,
                        "destination_root": destination_root,
                        "destination_relative_path": destination_relative,
                    },
                    requires_confirmation=True,
                )
                return {
                    "ok": True,
                    "tool_name": tool_name,
                    "args": args,
                    "proposal": proposal,
                    "result": {"status": "confirmation_required"},
                }
            if tool_name == "delete_file":
                root = self._required_root(context, args)
                relative_path = self._required_relative_path(context, args, "relative_path", "selected_file_path")
                proposal = AssistantActionProposal(
                    action_type="file_delete",
                    title=f"Delete file {relative_path}",
                    parameters={"root": root, "relative_path": relative_path},
                    requires_confirmation=True,
                )
                return {
                    "ok": True,
                    "tool_name": tool_name,
                    "args": args,
                    "proposal": proposal,
                    "result": {"status": "confirmation_required"},
                }
            if tool_name == "list_inbox":
                unread_raw = str(args.get("unread", "")).strip().lower()
                unread_only: bool | None
                if unread_raw == "unread":
                    unread_only = True
                elif unread_raw == "read":
                    unread_only = False
                else:
                    unread_only = None
                limit = int(args.get("limit", 25))
                messages = self._yahoo_service.list_inbox(
                    unread_only=unread_only,
                    sender=str(args.get("sender", "")),
                    subject_keyword=str(args.get("subject_keyword", "")),
                    limit=max(1, min(limit, 50)),
                )
                return {
                    "ok": True,
                    "tool_name": tool_name,
                    "args": args,
                    "result": {
                        "count": len(messages),
                        "emails": [
                            {
                                "uid": message.uid,
                                "subject": message.subject,
                                "sender": message.sender,
                                "received_at": message.received_at,
                                "unread": message.unread,
                            }
                            for message in messages
                        ],
                    },
                }
            if tool_name == "read_email":
                uid = str(args.get("uid") or context.selected_email_uid).strip()
                message = self._yahoo_service.read_email(uid)
                return {
                    "ok": True,
                    "tool_name": tool_name,
                    "args": args,
                    "result": {
                        "uid": message.uid,
                        "subject": message.subject,
                        "sender": message.sender,
                        "received_at": message.received_at,
                        "body_text": message.body_text[:12000],
                    },
                }
            if tool_name == "summarize_email":
                uid = str(args.get("uid") or context.selected_email_uid).strip()
                summary = self._yahoo_service.summarize_email(uid)
                return {"ok": True, "tool_name": tool_name, "args": args, "result": {"uid": uid, "summary": summary}}
            if tool_name == "draft_reply":
                uid = str(args.get("uid") or context.selected_email_uid).strip()
                notes = str(args.get("notes", ""))
                draft = self._yahoo_service.draft_reply(uid, notes)
                proposal = AssistantActionProposal(
                    action_type="email_use_draft",
                    title="Apply drafted reply to editor",
                    parameters={"to": draft.to_address, "subject": draft.subject, "body": draft.body},
                    requires_confirmation=False,
                )
                return {
                    "ok": True,
                    "tool_name": tool_name,
                    "args": args,
                    "proposal": proposal,
                    "result": {"status": "draft_ready", "to": draft.to_address, "subject": draft.subject},
                }
            if tool_name == "draft_new_email":
                to_address = str(args.get("to_address", "")).strip()
                subject = str(args.get("subject", "")).strip()
                notes = str(args.get("notes", ""))
                draft = self._yahoo_service.draft_new_email(to_address, subject, notes)
                proposal = AssistantActionProposal(
                    action_type="email_use_draft",
                    title="Apply drafted email to editor",
                    parameters={"to": draft.to_address, "subject": draft.subject, "body": draft.body},
                    requires_confirmation=False,
                )
                return {
                    "ok": True,
                    "tool_name": tool_name,
                    "args": args,
                    "proposal": proposal,
                    "result": {"status": "draft_ready", "to": draft.to_address, "subject": draft.subject},
                }
            if tool_name == "send_email":
                to_address = str(args.get("to_address", "")).strip()
                subject = str(args.get("subject", "")).strip()
                body = str(args.get("body", "")).strip()
                use_current = bool(args.get("use_current_draft", False))
                parameters = {"use_current_draft": "true"} if use_current else {
                    "to": to_address,
                    "subject": subject,
                    "body": body,
                }
                proposal = AssistantActionProposal(
                    action_type="email_send",
                    title="Send drafted email",
                    parameters=parameters,
                    requires_confirmation=True,
                )
                return {
                    "ok": True,
                    "tool_name": tool_name,
                    "args": args,
                    "proposal": proposal,
                    "result": {"status": "confirmation_required"},
                }
        except (FileOperationError, YahooMailError, ValueError) as exc:
            return {"ok": False, "tool_name": tool_name, "args": args, "error": str(exc)}

        return {"ok": False, "tool_name": tool_name, "args": args, "error": f"Unhandled tool: {tool_name}"}

    def _required_root(self, context: AssistantContext, args: dict[str, Any]) -> str:
        root = str(args.get("root") or context.selected_root).strip()
        if not root:
            raise ValueError("No approved root is selected.")
        return root

    def _required_relative_path(
        self,
        context: AssistantContext,
        args: dict[str, Any],
        arg_key: str,
        context_field: str,
    ) -> str:
        context_value = getattr(context, context_field)
        value = str(args.get(arg_key) or context_value).strip()
        if not value:
            raise ValueError(f"Missing required path: {arg_key}")
        return value

    def _context_labels(self, context: AssistantContext) -> list[str]:
        labels: list[str] = []
        if context.selected_root:
            labels.append(f"selected root {context.selected_root}")
        if context.selected_file_path:
            labels.append(f"selected file {context.selected_file_path}")
        if context.open_folder_path:
            if context.open_folder_path == ".":
                labels.append("open folder (approved root)")
            else:
                labels.append(f"open folder {context.open_folder_path}")
        elif context.selected_root:
            labels.append("open folder (approved root)")
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
