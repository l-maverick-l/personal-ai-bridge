from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class DraftFailureUpdate:
    draft_status: str
    yahoo_ai_status: str
    results_text: str
    fallback_draft_body: str | None
    assistant_note: str


def build_draft_failure_update(message: str, existing_draft_body: str, last_partial_draft: str) -> DraftFailureUpdate:
    fallback = None
    if not existing_draft_body.strip() and last_partial_draft.strip():
        fallback = last_partial_draft
    return DraftFailureUpdate(
        draft_status=f"draft failed — {message}",
        yahoo_ai_status="failed",
        results_text=f"Yahoo draft generation failed: {message}",
        fallback_draft_body=fallback,
        assistant_note=f"[Yahoo draft failed] {message}",
    )
