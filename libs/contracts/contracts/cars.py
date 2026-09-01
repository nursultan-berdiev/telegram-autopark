"""DTO машин и их состояния."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .common import DTO


class CarDTO(DTO):
    id: int
    plate: str
    model: str | None = None
    status: str
    photo_file_id: str | None = None
    photo_path: str | None = None
    created_at: datetime | None = None
    driver_id: int | None = None
    driver_name: str | None = None


class CarCreate(DTO):
    plate: str
    model: str | None = None
    photo_file_id: str | None = None
    photo_path: str | None = None


class CarStateDTO(DTO):
    """Снимок «где машина сейчас».

    online и last_point_age_seconds в БД не хранятся — считаются от last_ts,
    иначе офлайн машины остались бы «онлайн» навсегда (см. plan/02).
    """

    car_id: int
    tracker_id: int | None = None
    last_ts: datetime | None = None
    lat: float | None = None
    lon: float | None = None
    speed_knots: float | None = None
    ignition: bool | None = None
    motion: bool | None = None
    odometer_km: Decimal | None = None
    odometer_tracker_id: int | None = None
    odometer_trusted: bool = True
    engine_blocked: bool = False
    last_command: str | None = None
    online: bool = False
    last_point_age_seconds: int | None = None


class TrackerDTO(DTO):
    id: int
    car_id: int
    provider: str
    external_id: str
    config: dict | None = None
    active: bool = True


class TrackerUpsert(DTO):
    provider: str = "traccar"
    external_id: str
    config: dict | None = None
