from __future__ import annotations

import random
import threading
import time
from contextlib import suppress
from queue import Empty
from typing import Any

from ..utils.logging import debug
from .encoder import BatchJSONEncoder, SerializationError
from .http import ExportError, HTTPSender
from .queue import EventQueue


class ExportWorker:
    """Daemon worker that batches runtime events and exports fail-open."""

    def __init__(
        self,
        config: object,
        queue: EventQueue | None = None,
        sender: Any | None = None,
        encoder: BatchJSONEncoder | None = None,
    ) -> None:
        self.config = config
        self.queue = queue or EventQueue(getattr(config, "max_queue_size", 1024))
        self.sender = sender if sender is not None else HTTPSender(config)
        self.encoder = encoder or BatchJSONEncoder()
        self.batch_size = max(1, int(getattr(config, "export_batch_size", 50)))
        self.flush_interval = max(0.01, float(getattr(config, "export_flush_interval", 5.0)))
        self.shutdown_timeout = max(0.0, float(getattr(config, "export_shutdown_timeout", 5.0)))
        self.max_retries = max(1, int(getattr(config, "max_export_retries", 3)))
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._shutdown_deadline: float | None = None
        self._dropped_serialization = 0
        self._failed_exports = 0

    @property
    def dropped_serialization(self) -> int:
        return self._dropped_serialization

    @property
    def failed_exports(self) -> int:
        return self._failed_exports

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name="promptrail-exporter", daemon=True)
        self._thread.start()

    def enqueue(self, event: Any) -> bool:
        return self.queue.put_nowait(event)

    def shutdown(self, timeout: float | None = None) -> None:
        budget = self.shutdown_timeout if timeout is None else max(0.0, timeout)
        deadline = time.monotonic() + budget
        self._shutdown_deadline = deadline
        self._stop.set()
        if self._thread:
            self._thread.join(max(0.0, deadline - time.monotonic()))
            if self._thread.is_alive():
                return
        self._drain_until(deadline)
        with suppress(Exception):
            self.sender.close()

    def _run(self) -> None:
        batch: list[Any] = []
        next_flush = time.monotonic() + self.flush_interval
        while not self._stop.is_set():
            remaining = max(0.0, next_flush - time.monotonic())
            try:
                item = self.queue.get(timeout=min(remaining, self.flush_interval))
                batch.append(item)
                self.queue.task_done()
                if len(batch) >= self.batch_size:
                    self._send_with_backoff(batch)
                    batch = []
                    next_flush = time.monotonic() + self.flush_interval
            except Empty:
                if batch:
                    self._send_with_backoff(batch)
                    batch = []
                next_flush = time.monotonic() + self.flush_interval
            except Exception:
                continue
        if batch:
            self._send_with_backoff(batch)

    def _drain_until(self, deadline: float) -> None:
        batch: list[Any] = []
        while time.monotonic() < deadline:
            try:
                batch.append(self.queue.get_nowait())
                self.queue.task_done()
                if len(batch) >= self.batch_size:
                    self._send_with_backoff(batch, deadline=deadline)
                    batch = []
            except Empty:
                break
            except Exception:
                break
        if batch and time.monotonic() < deadline:
            self._send_with_backoff(batch, deadline=deadline)

    def _send_with_backoff(self, batch: list[Any], deadline: float | None = None) -> None:
        deadline = deadline or self._shutdown_deadline
        try:
            body = self.encoder.encode(batch)
        except SerializationError:
            self._dropped_serialization += len(batch)
            return
        delay = 0.1
        attempts = 0
        while attempts < self.max_retries:
            attempts += 1
            if deadline is not None and time.monotonic() >= deadline:
                self._failed_exports += len(batch)
                return
            try:
                self.sender.send(body, {"Content-Type": self.encoder.content_type})
                debug(
                    f"batch exported: {len(batch)} events",
                    enabled=bool(getattr(self.config, "debug", False)),
                )
                return
            except ExportError as exc:
                self._failed_exports += len(batch)
                if not exc.retryable:
                    return
            except Exception:
                self._failed_exports += len(batch)
            sleep_for = delay + random.uniform(0, delay / 4)
            if deadline is not None:
                sleep_for = min(sleep_for, max(0.0, deadline - time.monotonic()))
            if sleep_for <= 0:
                return
            time.sleep(sleep_for)
            delay = min(delay * 2, 5.0)


__all__ = ["ExportWorker"]
