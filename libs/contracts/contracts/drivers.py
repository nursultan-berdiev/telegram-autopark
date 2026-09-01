"""DTO водителей и приглашений."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .common import DTO


class DriverDTO(DTO):
    id: int
    tg_user_id: int
    full_name: str
    phone: str | None = None
    inn: str | None = None
    selfie_file_id: str | None = None
    selfie_path: str | None = None
    car_id: int | None = None
    car_plate: str | None = None
    active: bool = True
    fired_at: datetime | None = None
    created_at: datetime | None = None


class DriverStatsDTO(DTO):
    payments_count: int = 0
    total_paid: Decimal = Decimal("0.00")
    last_payment_at: datetime | None = None


class DriverWithStats(DTO):
    driver: DriverDTO
    stats: DriverStatsDTO


class DriverRegister(DTO):
    code: str
    tg_user_id: int
    full_name: str
    phone: str
    inn: str | None = None
    selfie_file_id: str | None = None
    selfie_path: str | None = None


class MeDTO(DTO):
    role: str
    driver: DriverDTO | None = None


class InvitationDTO(DTO):
    code: str
    car_id: int
    expires_at: datetime
    ttl_label: str


class InviteCheckDTO(DTO):
    ok: bool
    problem: str | None = None
    car_id: int | None = None
    car_plate: str | None = None
