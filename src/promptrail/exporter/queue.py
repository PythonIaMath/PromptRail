from __future__ import annotations

from queue import Empty, Full, Queue
from threading import Lock
from typing import Any


class EventQueue:
    """Bounded, non-blocking event queue with fail-open drop counters."""

    def __init__(self, maxsize: int = 1024) -> None:
        self._queue: Queue[Any] = Queue(maxsize=max(1, int(maxsize)))
        self._lock = Lock()
        self.dropped_full = 0
        self.dropped_error = 0

    def put_nowait(self, event: Any) -> bool:
        try:
            self._queue.put_nowait(event)
            return True
        except Full:
            with self._lock:
                self.dropped_full += 1
            return False
        except Exception:
            with self._lock:
                self.dropped_error += 1
            return False

    def get(self, timeout: float | None = None) -> Any:
        return self._queue.get(timeout=timeout)

    def get_nowait(self) -> Any:
        return self._queue.get_nowait()

    def task_done(self) -> None:
        try:
            self._queue.task_done()
        except ValueError:
            pass

    def empty(self) -> bool:
        return self._queue.empty()

    def qsize(self) -> int:
        return self._queue.qsize()

    @property
    def dropped(self) -> int:
        return self.dropped_full + self.dropped_error


__all__ = ["EventQueue", "Empty"]
