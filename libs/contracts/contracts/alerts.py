"""DTO правил и алертов."""
from __future__ import annotations

from datetime import datetime

from .common import DTO


class RuleDTO(DTO):
    id: int
    car_id: int | None = None
    type: str
    params: dict = {}
    enabled: bool = True
    severity: str = "warning"


class RuleUpsert(DTO):
    car_id: int | None = None
    type: str
    params: dict = {}
    enabled: bool = True
    severity: str = "warning"


class AlertDTO(DTO):
    id: int
    rule_id: int | None = None
    car_id: int
    car_plate: str | None = None
    type: str
    severity: str = "warning"
    status: str = "open"
    triggered_at: datetime
    last_seen_at: datetime | None = None
    resolved_at: datetime | None = None
    payload: dict = {}
    action_taken: str | None = None
    text: str = ""
