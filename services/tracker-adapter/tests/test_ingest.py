"""IngestWorker: батч-флаш по размеру/таймауту, ретрай буфера при ошибке core-api."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime, timezone

import pytest

from app.ingest import IngestWorker
from app.providers.base import NormalizedPoint, TrackerProvider


def _point(external_id: str) -> NormalizedPoint:
    now = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    return NormalizedPoint(
        external_id=external_id,
        ts=now,
        server_ts=now,
        lat=42.0,
        lon=74.0,
        speed_knots=0.0,
        course=0.0,
        altitude=0.0,
        valid=True,
        ignition=None,
        motion=None,
        total_distance_km=None,
        engine_blocked=None,
        status_raw=None,
    )


class QueueProvider(TrackerProvider):
    """Фейковый провайдер: тест кладёт точки в queue, stream() их отдаёт."""

    name = "fake"

    def __init__(self) -> None:
        self.queue: asyncio.Queue[NormalizedPoint] = asyncio.Queue()

    async def list_devices(self) -> list[dict]:
        return []

    async def get_state(self, external_id: str) -> NormalizedPoint | None:
        return None

    async def stream(self) -> AsyncIterator[NormalizedPoint]:
        while True:
            yield await self.queue.get()

    async def send_command(self, external_id, cmd, params=None) -> dict:
        return {"status": "sent", "result": None}


class FlakyOnceProvider(TrackerProvider):
    """Первый stream() рвётся сразу; второй — обычный QueueProvider-стрим."""

    name = "flaky"

    def __init__(self) -> None:
        self.queue: asyncio.Queue[NormalizedPoint] = asyncio.Queue()
        self.attempts = 0

    async def list_devices(self) -> list[dict]:
        return []

    async def get_state(self, external_id: str) -> NormalizedPoint | None:
        return None

    async def stream(self) -> AsyncIterator[NormalizedPoint]:
        self.attempts += 1
        if self.attempts == 1:
            raise RuntimeError("ws dropped")
        yield await self.queue.get()  # pragma: no cover - unreachable without second attempt

    async def send_command(self, external_id, cmd, params=None) -> dict:
        return {"status": "sent", "result": None}


class FakeCoreApi:
    def __init__(self, fail_times: int = 0) -> None:
        self.batches: list[list[NormalizedPoint]] = []
        self.calls = 0
        self.fail_times = fail_times
        self.flushed = asyncio.Event()

    async def push_telemetry_batch(self, points: list[NormalizedPoint]) -> None:
        self.calls += 1
        if self.calls <= self.fail_times:
            raise RuntimeError("core-api unavailable")
        self.batches.append(list(points))
        self.flushed.set()


async def test_is_running_reflects_task_lifecycle():
    provider = QueueProvider()
    core_api = FakeCoreApi()
    worker = IngestWorker(provider, core_api, batch_size=100, flush_seconds=1000)

    assert worker.is_running is False
    await worker.start()
    assert worker.is_running is True
    await worker.stop()
    assert worker.is_running is False


async def test_flushes_when_batch_size_reached():
    provider = QueueProvider()
    core_api = FakeCoreApi()
    worker = IngestWorker(provider, core_api, batch_size=2, flush_seconds=1000)
    await worker.start()
    try:
        provider.queue.put_nowait(_point("a"))
        provider.queue.put_nowait(_point("b"))
        await asyncio.wait_for(core_api.flushed.wait(), timeout=2)
    finally:
        await worker.stop()

    assert len(core_api.batches) == 1
    assert [p.external_id for p in core_api.batches[0]] == ["a", "b"]
    assert worker.points_sent == 2


async def test_flushes_on_timeout_with_partial_buffer():
    provider = QueueProvider()
    core_api = FakeCoreApi()
    worker = IngestWorker(provider, core_api, batch_size=100, flush_seconds=0.05)
    await worker.start()
    try:
        provider.queue.put_nowait(_point("a"))
        await asyncio.wait_for(core_api.flushed.wait(), timeout=2)
    finally:
        await worker.stop()

    assert len(core_api.batches) == 1
    assert [p.external_id for p in core_api.batches[0]] == ["a"]


async def test_retries_buffer_after_core_api_failure_without_losing_points():
    provider = QueueProvider()
    core_api = FakeCoreApi(fail_times=1)  # первая попытка флаша падает
    worker = IngestWorker(provider, core_api, batch_size=1, flush_seconds=1000)
    await worker.start()
    try:
        provider.queue.put_nowait(_point("a"))
        # ждём первую (неудачную) попытку, не дожидаясь успеха
        for _ in range(50):
            if core_api.calls >= 1:
                break
            await asyncio.sleep(0.02)
        assert core_api.calls == 1
        assert core_api.batches == []  # ничего не улетело — буфер не потерян

        provider.queue.put_nowait(_point("b"))
        await asyncio.wait_for(core_api.flushed.wait(), timeout=2)
    finally:
        await worker.stop()

    assert [p.external_id for p in core_api.batches[0]] == ["a", "b"]


async def test_stop_flushes_remaining_buffer():
    provider = QueueProvider()
    core_api = FakeCoreApi()
    worker = IngestWorker(provider, core_api, batch_size=100, flush_seconds=1000)
    await worker.start()
    provider.queue.put_nowait(_point("a"))
    # дать read/flush тасkам шанс переложить точку из queue в буфер до остановки
    for _ in range(50):
        if worker._buffer:
            break
        await asyncio.sleep(0.02)
    await worker.stop()

    assert len(core_api.batches) == 1
    assert core_api.batches[0][0].external_id == "a"


async def test_gap_is_counted_and_stream_restarts_after_error():
    provider = FlakyOnceProvider()
    core_api = FakeCoreApi()
    worker = IngestWorker(provider, core_api, batch_size=1, flush_seconds=1000)
    await worker.start()
    try:
        provider.queue.put_nowait(_point("a"))
        await asyncio.wait_for(core_api.flushed.wait(), timeout=3)
    finally:
        await worker.stop()

    assert worker.gaps == 1
    assert core_api.batches[0][0].external_id == "a"


@pytest.fixture(autouse=True)
def _short_gap_backoff(monkeypatch):
    """Не ждать реальную секунду бэкоффа между попытками переподключения стрима."""
    real_sleep = asyncio.sleep

    async def fast_sleep(seconds: float) -> None:
        await real_sleep(min(seconds, 0.05))

    monkeypatch.setattr("app.ingest.asyncio.sleep", fast_sleep)
