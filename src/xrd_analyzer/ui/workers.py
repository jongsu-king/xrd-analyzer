"""Background analysis worker (QThread) with progress, cancel and pause.

The analysis core is pure Python/NumPy and never touches Qt; this worker only calls it
and forwards results through queued signals, so the GUI never freezes.
"""

from __future__ import annotations

import threading
import time
import traceback
from collections.abc import Callable
from typing import Any

from PySide6.QtCore import QObject, QThread, Signal

from ..core.fit import AnalysisCancelled


class Worker(QObject):
    progress = Signal(float, str)
    finished = Signal(object)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, job: Callable[..., Any]) -> None:
        super().__init__()
        self._job = job
        self._cancel = threading.Event()
        self._pause = threading.Event()

    # called from the GUI thread
    def request_cancel(self) -> None:
        self._cancel.set()
        self._pause.clear()

    def toggle_pause(self) -> bool:
        if self._pause.is_set():
            self._pause.clear()
        else:
            self._pause.set()
        return self._pause.is_set()

    # called from the worker thread at every checkpoint of the analysis
    def _checkpoint(self) -> bool:
        while self._pause.is_set() and not self._cancel.is_set():
            time.sleep(0.05)
        return self._cancel.is_set()

    def run(self) -> None:
        try:
            out = self._job(progress=lambda f, m: self.progress.emit(f, m), cancel=self._checkpoint)
        except AnalysisCancelled:
            self.cancelled.emit()
        except Exception as exc:  # report every failure to the GUI instead of crashing
            self.failed.emit(f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}")
        else:
            self.finished.emit(out)


def make_worker(parent: QObject, job: Callable[..., Any]) -> tuple[QThread, Worker]:
    """Create (but do NOT start) a worker thread. The caller connects its receivers first and
    then calls ``thread.start()``, so no terminal signal can be emitted before it is heard."""
    thread = QThread(parent)
    worker = Worker(job)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    for sig in (worker.finished, worker.failed, worker.cancelled):
        sig.connect(thread.quit)
    thread.finished.connect(worker.deleteLater)
    return thread, worker
