"""DTO штрафов и обслуживания."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import Field

from .common import DTO


class FineDTO(DTO):
    id: int
    car_id: int
    driver_id: int | None = None
    amount: Decimal | None = None
    currency: str | None = None
    issued_at: datetime
    status: str = "unpaid"
    paid_at: datetime | None = None
    source: str = "manual"
    external_ref: str | None = None
    note: str | None = None
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
    currency: str | None = Field(default=None, max_length=8)
    issued_at: datetime | None = None
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
