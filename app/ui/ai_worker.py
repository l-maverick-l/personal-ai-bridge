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
            result = self._task(self.status.emit, self.partial.emit, self._cancel_event.is_set)
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(
                {
                    "exception_type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                }
            )
        finally:
            self.finished.emit()
