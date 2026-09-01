"""Интерфейс провайдера трекеров + общие DTO.

libs/contracts ещё не заведён в этом дереве — DTO объявлены здесь локально.
При появлении libs/contracts.TelemetryPoint NormalizedPoint должен маппиться в него 1:1
(см. план 04-tracker-adapter.md), но сам адаптер от contracts не зависит.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from enum import Enum


class TrackerCommand(str, Enum):
    ENGINE_STOP = "engine_stop"
    ENGINE_RESUME = "engine_resume"
    ALARM_ARM = "alarm_arm"
    ALARM_DISARM = "alarm_disarm"


@dataclass
class NormalizedPoint:
    """Нормализованная точка телеметрии — единый формат для всех провайдеров."""

    external_id: str
    ts: datetime
    server_ts: datetime
    lat: float | None
    lon: float | None
    speed_knots: float | None
    course: float | None
    altitude: float | None
    valid: bool
    ignition: bool | None
    motion: bool | None
    total_distance_km: Decimal | None
    engine_blocked: bool | None
    status_raw: str | None
    attributes: dict = field(default_factory=dict)


class TrackerProvider(ABC):
    """Точка расширяемости: новый тип трекера = новый класс с этим интерфейсом.

    core-api и остальной адаптер работают с провайдером единообразно — конкретная
    реализация выбирается только в registry.py по строке `provider`.
    """

    name: str

    @abstractmethod
    async def list_devices(self) -> list[dict]: ...

    @abstractmethod
    async def get_state(self, external_id: str) -> NormalizedPoint | None: ...

    @abstractmethod
    def stream(self) -> AsyncIterator[NormalizedPoint]:
        """Живой поток нормализованных точек (WS-подписка или поллинг-фолбэк)."""
        ...

    @abstractmethod
    async def send_command(
        self,
        external_id: str,
        cmd: TrackerCommand,
        params: dict | None = None,
    ) -> dict:
        """Возвращает {status: sent|failed, result: <ответ трекера/провайдера>}."""
        ...
