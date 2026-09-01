"""DTO телеметрии (ингест от адаптера)."""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from .common import DTO


class TelemetryPoint(DTO):
    external_id: str
    ts: datetime
    server_ts: datetime | None = None
    lat: float | None = None
    lon: float | None = None
    speed_knots: float | None = None
    course: float | None = None
    altitude: float | None = None
    valid: bool = True
    ignition: bool | None = None
    motion: bool | None = None
    total_distance_km: Decimal | None = None
    engine_blocked: bool | None = None
    status_raw: str | None = None
    attributes: dict | None = None


class TelemetryBatchResult(DTO):
    accepted: int = 0
    unknown_devices: list[str] = []
