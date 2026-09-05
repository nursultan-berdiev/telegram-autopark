"""Чтение алертов и смена их статуса."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_core
from app.db.models import Alert, AlertStatus, Car
from app.db.session import get_session
from app.domain import alerts as alerts_domain
from app.errors import NotFound
from contracts import AlertDTO

router = APIRouter()


async def _to_dto(session: AsyncSession, alert: Alert) -> AlertDTO:
    car = await session.get(Car, alert.car_id)
    payload = alert.payload or {}
    return AlertDTO(
        id=alert.id,
        rule_id=alert.rule_id,
        car_id=alert.car_id,
        car_plate=car.plate if car else None,
        type=alert.type.value,
        severity=alert.severity,
        status=alert.status.value,
        triggered_at=alert.triggered_at,
        last_seen_at=alert.last_seen_at,
        resolved_at=alert.resolved_at,
        payload=payload,
        action_taken=alert.action_taken,
        text=payload.get("text", ""),
    )


@router.get("", response_model=list[AlertDTO])
async def list_alerts(
    status: str | None = "open",
    car_id: int | None = None,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> list[AlertDTO]:
    alerts = await alerts_domain.list_alerts(session, status=status, car_id=car_id)
    return [await _to_dto(session, alert) for alert in alerts]


@router.post("/{alert_id}/ack", response_model=AlertDTO)
async def ack_alert(
    alert_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> AlertDTO:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise NotFound("алерт не найден")
    await alerts_domain.set_status(session, alert, AlertStatus.acknowledged)
    await session.commit()
    return await _to_dto(session, alert)


@router.post("/{alert_id}/resolve", response_model=AlertDTO)
async def resolve_alert(
    alert_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> AlertDTO:
    alert = await session.get(Alert, alert_id)
    if alert is None:
        raise NotFound("алерт не найден")
    await alerts_domain.set_status(session, alert, AlertStatus.resolved)
    await session.commit()
    return await _to_dto(session, alert)
