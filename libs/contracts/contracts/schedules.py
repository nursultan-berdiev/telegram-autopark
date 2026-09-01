"""DTO графиков платежей."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .common import DTO


class ScheduleDTO(DTO):
    id: int
    driver_id: int
    period: str
    interval_days: int | None = None
    amount: Decimal
    paid_in_period: Decimal = Decimal("0.00")
    next_due_date: datetime
    active: bool = True


class ScheduleStatusDTO(DTO):
    """Расчётное состояние графика — считает core-api, бот только рисует."""

    next_due_date: datetime
    amount: Decimal
    paid_in_period: Decimal
    remaining_current: Decimal
    overdue_periods: int
    overdue_days: int
    debt_now: Decimal
    is_overdue: bool
    summary: str
    period_label: str


class ScheduleWithStatus(DTO):
    schedule: ScheduleDTO | None = None
    status: ScheduleStatusDTO | None = None


class ScheduleUpsert(DTO):
    period: str
    interval_days: int | None = None
    amount: Decimal
    next_due_date: datetime
