"""DTO штрафов и обслуживания."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

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
