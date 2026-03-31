from __future__ import annotations

import threading
import traceback
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal, Slot


class AIWorker(QObject):
    status = Signal(str)
    partial = Signal(str)
    completed = Signal(object)
    failed = Signal(object)
    finished = Signal()

    def __init__(self, task: Callable[[Callable[[str], None], Callable[[str], None], Callable[[], bool]], object]) -> None:
        super().__init__()
        self._task = task
        self._cancel_event = threading.Event()

    def cancel(self) -> None:
        self._cancel_event.set()

    @Slot()
    def run(self) -> None:
        try:
            result = self._task(self._emit_status, self._emit_partial, self._cancel_event.is_set)
            self.completed.emit(result)
        except Exception as exc:
            self._emit_failure_payload(exc)
        finally:
            try:
                self.finished.emit()
            except Exception:
                # Never let Qt signal edge cases terminate the worker thread.
                pass

    def _emit_status(self, text: str) -> None:
        try:
            self.status.emit(text)
        except Exception as exc:
            raise RuntimeError(f"status_emit_failed: {exc}") from exc

    def _emit_partial(self, text: str) -> None:
        try:
            self.partial.emit(text)
        except Exception as exc:
            raise RuntimeError(f"partial_emit_failed: {exc}") from exc

    def _emit_failure_payload(self, exc: Exception) -> None:
        try:
            self.failed.emit(
                {
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
        except Exception:
            pass
