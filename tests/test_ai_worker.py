from __future__ import annotations

import unittest

from app.ui.ai_worker import AIWorker


class AIWorkerTests(unittest.TestCase):
    def test_worker_emits_partial_and_completion_without_crashing(self) -> None:
        statuses: list[str] = []
        partials: list[str] = []
        completed: list[object] = []
        failures: list[object] = []
        finished_count = 0

        def task(on_status, on_partial, is_cancelled):  # noqa: ANN001
            _ = is_cancelled
            on_status("connecting")
            on_partial("h")
            on_partial("he")
            on_partial("hello")
            return "hello"

        worker = AIWorker(task)
        worker.status.connect(statuses.append)
        worker.partial.connect(partials.append)
        worker.completed.connect(completed.append)
        worker.failed.connect(failures.append)

        def _on_finished() -> None:
            nonlocal finished_count
            finished_count += 1

        worker.finished.connect(_on_finished)
        worker.run()

        self.assertEqual(statuses, ["connecting"])
        self.assertEqual(partials[-1], "hello")
        self.assertEqual(completed, ["hello"])
        self.assertEqual(failures, [])
        self.assertEqual(finished_count, 1)

    def test_worker_failure_is_captured_as_payload(self) -> None:
        failures: list[object] = []
        finished_count = 0

        def task(on_status, on_partial, is_cancelled):  # noqa: ANN001
            _ = on_status, on_partial, is_cancelled
            raise RuntimeError("boom")

        worker = AIWorker(task)
        worker.failed.connect(failures.append)

        def _on_finished() -> None:
            nonlocal finished_count
            finished_count += 1

        worker.finished.connect(_on_finished)
        worker.run()

        self.assertEqual(len(failures), 1)
        self.assertIsInstance(failures[0], dict)
        payload = failures[0]
        self.assertEqual(payload["exception_type"], "RuntimeError")
        self.assertEqual(payload["message"], "boom")
        self.assertIn("Traceback", payload["traceback"])
        self.assertEqual(finished_count, 1)

    def test_worker_survives_failed_signal_handler_exceptions(self) -> None:
        finished_count = 0

        def task(on_status, on_partial, is_cancelled):  # noqa: ANN001
            _ = on_status, on_partial, is_cancelled
            raise RuntimeError("task exploded")

        worker = AIWorker(task)

        def _broken_failed_handler(_: object) -> None:
            raise RuntimeError("downstream failed handler broke")

        worker.failed.connect(_broken_failed_handler)

        def _on_finished() -> None:
            nonlocal finished_count
            finished_count += 1

        worker.finished.connect(_on_finished)
        worker.run()

        self.assertEqual(finished_count, 1)


if __name__ == "__main__":
    unittest.main()
