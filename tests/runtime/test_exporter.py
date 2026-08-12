from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass

import pytest

from promptrail.exporter.encoder import BatchJSONEncoder, SerializationError
from promptrail.exporter.http import ExportError, HTTPSender
from promptrail.exporter.queue import EventQueue
from promptrail.exporter.worker import ExportWorker


@dataclass
class Config:
    api_key: str | None = "pr_test"
    endpoint: str = "https://runtime.promptrail.test/v1/runtime/events"
    max_queue_size: int = 2
    export_batch_size: int = 2
    export_flush_interval: float = 0.01
    export_shutdown_timeout: float = 0.2
    export_gzip: bool = False
    export_timeout: float = 1.0
    sdk_version: str = "test"


class Event:
    def __init__(self, event_id: int) -> None:
        self.event_id = event_id

    def to_dict(self):
        return {"id": self.event_id, "type": "test"}


class BadEvent:
    def to_dict(self):
        return {"bad": object()}


class FakeSender:
    def __init__(self, failures: int = 0) -> None:
        self.failures = failures
        self.bodies: list[bytes] = []
        self.headers: list[dict[str, str]] = []
        self.closed = False

    def send(self, body: bytes, headers):
        self.headers.append(dict(headers))
        if self.failures:
            self.failures -= 1
            raise ExportError("temporary", retryable=True)
        self.bodies.append(body)
        return None

    def close(self) -> None:
        self.closed = True


def decoded_batches(sender: FakeSender):
    return [json.loads(body.decode("utf-8"))["events"] for body in sender.bodies]


def test_event_queue_is_bounded_and_counts_full_drops():
    queue = EventQueue(maxsize=1)

    assert queue.put_nowait(Event(1)) is True
    assert queue.put_nowait(Event(2)) is False

    assert queue.dropped_full == 1
    assert queue.dropped == 1
    assert queue.qsize() == 1


def test_worker_retries_retryable_export_failure(monkeypatch):
    monkeypatch.setattr("promptrail.exporter.worker.time.sleep", lambda _: None)
    sender = FakeSender(failures=1)
    worker = ExportWorker(Config(export_batch_size=2), sender=sender)

    worker._send_with_backoff([Event(1), Event(2)], deadline=time.monotonic() + 1)

    assert sender.failures == 0
    assert len(sender.bodies) == 1
    assert decoded_batches(sender) == [[{"id": 1, "type": "test"}, {"id": 2, "type": "test"}]]
    assert worker.failed_exports == 2


def test_batch_encoder_sanitizes_serialization_failure():
    encoder = BatchJSONEncoder()

    with pytest.raises(SerializationError) as exc:
        encoder.encode([BadEvent()])

    assert "object at" not in str(exc.value)
    assert "failed to serialize runtime event batch" in str(exc.value)


def test_worker_drops_serialization_failure_fail_open():
    sender = FakeSender()
    worker = ExportWorker(Config(), sender=sender)

    worker._send_with_backoff([BadEvent()], deadline=time.monotonic() + 1)

    assert worker.dropped_serialization == 1
    assert sender.bodies == []


def test_shutdown_flushes_queued_events_and_closes_sender():
    sender = FakeSender()
    worker = ExportWorker(Config(export_batch_size=10, export_shutdown_timeout=1), sender=sender)

    assert worker.enqueue(Event(1)) is True
    assert worker.enqueue(Event(2)) is True
    worker.shutdown(timeout=1)

    assert decoded_batches(sender) == [[{"id": 1, "type": "test"}, {"id": 2, "type": "test"}]]
    assert sender.closed is True


def test_worker_flushes_by_batch_size_on_daemon_thread():
    sender = FakeSender()
    worker = ExportWorker(Config(export_batch_size=2, export_flush_interval=1), sender=sender)
    worker.start()
    try:
        assert worker.enqueue(Event(1)) is True
        assert worker.enqueue(Event(2)) is True
        deadline = time.monotonic() + 1
        while not sender.bodies and time.monotonic() < deadline:
            time.sleep(0.01)
        assert decoded_batches(sender) == [[{"id": 1, "type": "test"}, {"id": 2, "type": "test"}]]
    finally:
        worker.shutdown(timeout=1)


def test_shutdown_timeout_does_not_drain_concurrently_with_blocked_worker():
    class BlockingSender(FakeSender):
        def __init__(self) -> None:
            super().__init__()
            self.started = threading.Event()
            self.release = threading.Event()
            self.active = 0
            self.max_active = 0

        def send(self, body: bytes, headers):
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.started.set()
            self.release.wait(timeout=1)
            self.active -= 1

    sender = BlockingSender()
    worker = ExportWorker(Config(export_batch_size=1, export_flush_interval=0.01), sender=sender)
    worker.start()
    assert worker.enqueue(Event(1))
    assert sender.started.wait(timeout=1)

    started = time.monotonic()
    worker.shutdown(timeout=0.02)
    elapsed = time.monotonic() - started

    assert elapsed < 0.1
    assert sender.max_active == 1
    assert sender.closed is False
    sender.release.set()
    worker.shutdown(timeout=0.2)
    assert sender.closed is True


def test_http_sender_rejects_redirects() -> None:
    class Response:
        status = 302

        def read(self) -> bytes:
            return b""

    class Connection:
        def request(self, *args, **kwargs) -> None:
            pass

        def getresponse(self) -> Response:
            return Response()

        def close(self) -> None:
            pass

    sender = HTTPSender(Config())
    sender._conn = Connection()  # type: ignore[assignment]
    with pytest.raises(ExportError) as exc:
        sender.send(b"{}")
    assert exc.value.retryable is False
