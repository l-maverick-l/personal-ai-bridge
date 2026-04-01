from __future__ import annotations

import unittest

from app.ui.draft_failure import build_draft_failure_update


class MainWindowDraftFailureTests(unittest.TestCase):
    def test_yahoo_draft_failure_fallback_preserves_partial_body(self) -> None:
        update = build_draft_failure_update(
            message="RuntimeError: boom",
            existing_draft_body="",
            last_partial_draft="partial draft text",
        )

        self.assertEqual(update.draft_status, "draft failed — RuntimeError: boom")
        self.assertEqual(update.yahoo_ai_status, "failed")
        self.assertEqual(update.fallback_draft_body, "partial draft text")
        self.assertIn("Yahoo draft generation failed", update.results_text)
        self.assertIn("Yahoo draft failed", update.assistant_note)


if __name__ == "__main__":
    unittest.main()
