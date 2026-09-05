"""DTO исходящих команд на трекер."""
from __future__ import annotations

from datetime import datetime

from .common import DTO


class CommandRequest(DTO):
    type: str  # engine_block | engine_unblock | alarm_arm | alarm_disarm
    requested_by: int | None = None
    alert_id: int | None = None


class CommandDTO(DTO):
    id: int
    car_id: int
    tracker_id: int | None = None
    type: str
    status: str
    requested_by: int | None = None
    alert_id: int | None = None
    safety_snapshot: dict | None = None
    result: str | None = None
    created_at: datetime | None = None
    acked_at: datetime | None = None


class CommandResult(DTO):
    """Итог попытки: отказ гейта — это не ошибка, а статус blocked_by_safety."""

    command: CommandDTO
    ok: bool
    reason: str | None = None
