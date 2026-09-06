"""DTO штрафов и обслуживания."""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import Field

from .common import DTO


class FineDTO(DTO):
    id: int
    car_id: int
    driver_id: int | None = None
    amount: Decimal | None = None
    # Скидка есть не у всех источников: у carcheck и ручного ввода её нет,
    # и пустое здесь означает «неизвестно», а не «скидки нет».
    amount_to_pay: Decimal | None = None
    discount_days_left: int | None = None
    currency: str | None = None
    issued_at: datetime
    status: str = "unpaid"
    paid_at: datetime | None = None
    source: str = "manual"
    external_ref: str | None = None
    article: str | None = None
    violation_title: str | None = None
    place: str | None = None
    payment_code: str | None = None
    protocol_kind: str | None = None
    delivery_date: date | None = None
    # Пусто, если постановление не вручено: срок скидки тогда не идёт.
    discount_until: date | None = None
    last_seen_at: datetime | None = None
    last_seen_source: str | None = None
    paid_by: str | None = None
    note: str | None = None
    # Заполняется только там, где список идёт по нескольким машинам.
    car_plate: str | None = None
    created_at: datetime | None = None


class FineCreate(DTO):
    driver_id: int | None = None
    amount: Decimal | None = None
    currency: str | None = None
    issued_at: datetime | None = None
    external_ref: str | None = None
    note: str | None = None


class FineImportItem(DTO):
    """Штраф, найденный внешним источником: машина ищется по госномеру."""

    plate: str = Field(min_length=1, max_length=32)
    # Длина под колонку в БД: молчаливая обрезка сломала бы идемпотентность.
    external_ref: str = Field(min_length=1, max_length=64)
    amount: Decimal | None = None
    # Необязательны: раннер в браузере отдаёт carcheck-форму без сумм, и
    # старый клиент должен продолжать работать без правок.
    amount_to_pay: Decimal | None = None
    discount_days_left: int | None = None
    currency: str | None = Field(default=None, max_length=8)
    issued_at: datetime | None = None
    article: str | None = Field(default=None, max_length=64)
    violation_title: str | None = None
    place: str | None = None
    payment_code: str | None = Field(default=None, max_length=32)
    protocol_kind: str | None = Field(default=None, max_length=16)
    delivery_date: date | None = None
    note: str | None = None


class FineImportResult(DTO):
    """Итог пакетного импорта.

    `created` — заведено новых, `skipped` — уже были в базе по номеру
    постановления, `unknown_plates` — номера не из нашего парка,
    `ambiguous_plates` — номера, сходящиеся сразу с двумя машинами парка.
    """

    created: int = 0
    skipped: int = 0
    unknown_plates: list[str] = []
    # Номер сходится с двумя машинами парка — импортировать наугад нельзя.
    ambiguous_plates: list[str] = []


class PeriodicTaskDTO(DTO):
    """Расписание фоновой задачи, как его видит админка."""

    id: int
    name: str
    task: str
    interval_seconds: int | None = None
    crontab: str | None = None
    args: dict | None = None
    enabled: bool = True
    last_run_at: datetime | None = None
    total_run_count: int = 0
    created_at: datetime | None = None


class PeriodicTaskUpsert(DTO):
    name: str
    task: str
    interval_seconds: int | None = None
    crontab: str | None = None
    args: dict | None = None
    enabled: bool = True


class PeriodicTaskPatch(DTO):
    """Частичное обновление: незаданные поля не трогаются."""

    name: str | None = None
    task: str | None = None
    interval_seconds: int | None = None
    crontab: str | None = None
    args: dict | None = None
    enabled: bool | None = None


class TaskRunDTO(DTO):
    """Исход прогона.

    `refused` — сервис ответил отказом (капча, блокировка); это не то же
    самое, что `ok` с пустым результатом, и путать их нельзя.
    """

    id: int
    task: str
    periodic_task_id: int | None = None
    status: str
    started_at: datetime
    finished_at: datetime | None = None
    detail: str | None = None
    payload: dict | None = None


class MaintenanceDTO(DTO):
    id: int
    car_id: int
    type: str
    interval_km: Decimal
    last_service_km: Decimal
    last_service_tracker_id: int | None = None
    last_service_at: datetime | None = None
    note: str | None = None
    over_km: Decimal | None = None


class MaintenanceUpsert(DTO):
    type: str
    interval_km: Decimal
    last_service_km: Decimal | None = None
    note: str | None = None
