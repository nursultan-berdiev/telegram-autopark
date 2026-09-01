"""DTO отчётов и напоминаний."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from .common import DTO


class CarDriverRow(DTO):
    plate: str
    model: str | None = None
    status: str
    driver_name: str | None = None
    driver_phone: str | None = None


class UpcomingRow(DTO):
    driver_id: int
    driver_name: str
    plate: str | None = None
    next_due_date: datetime
    amount: Decimal
    debt_now: Decimal
    is_overdue: bool
    overdue_days: int = 0
    summary: str


class DriverTotalRow(DTO):
    driver_id: int
    driver_name: str
    car_plate: str | None = None
    payments_count: int
    total: Decimal


class CarTotalRow(DTO):
    car_id: int
    plate: str
    payments_count: int
    total: Decimal


class ReminderItem(DTO):
    schedule_id: int
    driver_id: int
    tg_user_id: int
    kind: str
    text: str


class ReminderPlanDTO(DTO):
    reminders: list[ReminderItem] = []
    owner_digest: list[str] = []
    today: date | None = None


class ReminderMark(DTO):
    schedule_ids: list[int]
    # По умолчанию дату ставит сервер: у клиента может быть другая таймзона,
    # и тогда антиспам «раз в день» молча перестаёт работать.
    on_date: date | None = None


class AssistantQuery(DTO):
    question: str


class AssistantAnswer(DTO):
    answer: str
