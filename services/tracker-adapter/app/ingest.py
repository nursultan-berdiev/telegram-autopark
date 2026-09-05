"""Фоновый ингест-воркер: provider.stream() -> буфер -> батч-пуш в core-api.

Чтение потока и батчинг разнесены по очереди (asyncio.Queue), т.к. asyncio.wait_for
поверх async-generator.__anext__() при таймауте способен оставить генератор в
недоопределённом состоянии — Queue.get() отменяется безопасно.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging

from app.clients.core_api import CoreApiClient
from app.providers.base import NormalizedPoint, TrackerProvider

logger = logging.getLogger(__name__)

# Сколько батчей держим при недоступном core-api, прежде чем ронять старые точки.
MAX_BUFFER_BATCHES = 200


class IngestWorker:
    def __init__(
        self,
        provider: TrackerProvider,
        core_api: CoreApiClient,
        *,
        batch_size: int,
        flush_seconds: float,
    ) -> None:
        self._provider = provider
        self._core_api = core_api
        self._batch_size = batch_size
        self._flush_seconds = flush_seconds
        # Простой core-api не должен съедать память адаптера: держим окно
        # свежих точек, самые старые отбрасываем с явным счётчиком потерь.
        self._max_buffer = max(batch_size * MAX_BUFFER_BATCHES, batch_size)
        self._buffer: list[NormalizedPoint] = []
        self.dropped = 0
        self._queue: asyncio.Queue[NormalizedPoint] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._flush_task: asyncio.Task | None = None
        self.points_sent = 0
        self.gaps = 0

    @property
    def is_running(self) -> bool:
        return bool(
            self._flush_task and not self._flush_task.done()
            and self._reader_task and not self._reader_task.done()
        )

    async def start(self) -> None:
        self._reader_task = asyncio.create_task(self._read_stream())
        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        for task in (self._reader_task, self._flush_task):
            if task is None:
                continue
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        # Не терять то, что успело накопиться в буфере на момент остановки.
        if self._buffer:
            await self._flush()

    async def _read_stream(self) -> None:
        while True:
            try:
                async for point in self._provider.stream():
                    await self._queue.put(point)
            except asyncio.CancelledError:
                raise
            except Exception:
                self.gaps += 1
                logger.exception("tracker stream broke (gap #%d), restarting", self.gaps)
                await asyncio.sleep(1.0)

    async def _flush_loop(self) -> None:
        while True:
            try:
                point = await asyncio.wait_for(self._queue.get(), timeout=self._flush_seconds)
                self._buffer.append(point)
                if len(self._buffer) >= self._batch_size:
                    await self._flush()
            except asyncio.TimeoutError:
                if self._buffer:
                    await self._flush()

    async def _flush(self) -> None:
        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        try:
            await self._core_api.push_telemetry_batch(batch)
            self.points_sent += len(batch)
            logger.info("flushed %d points to core-api (total=%d)", len(batch), self.points_sent)
        except Exception:
            logger.warning("core-api push failed, keeping %d points for retry", len(batch), exc_info=True)
            # Не терять точки: возвращаем их в начало буфера, следующий флаш попробует снова.
            self._buffer = batch + self._buffer
            if len(self._buffer) > self._max_buffer:
                lost = len(self._buffer) - self._max_buffer
                self._buffer = self._buffer[-self._max_buffer:]
                self.dropped += lost
                logger.warning(
                    "буфер телеметрии переполнен, отброшено точек: %d (всего %d)",
                    lost,
                    self.dropped,
                )
