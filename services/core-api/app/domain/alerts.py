"""Жизненный цикл алертов.

Дедуп открытых держит БД (два частичных индекса), поэтому вставку делаем
идемпотентно: при конфликте обновляем payload и last_seen_at, а
triggered_at не трогаем — иначе «висит с 20.08» станет «минуту назад».
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Alert, AlertStatus, AlertType
from app.errors import DomainError


async def _find_open(
    session: AsyncSession, *, car_id: int, atype: AlertType, rule_id: int | None
) -> Alert | None:
    query = select(Alert).where(
        Alert.car_id == car_id, Alert.status == AlertStatus.open
    )
    query = (
        query.where(Alert.rule_id == rule_id)
        if rule_id is not None
        else query.where(Alert.rule_id.is_(None), Alert.type == atype)
    )
    return await session.scalar(query)


async def raise_alert(
    session: AsyncSession,
    *,
    car_id: int,
    atype: AlertType,
    payload: dict,
    text: str,
    severity: str = "warning",
    rule_id: int | None = None,
    now: datetime | None = None,
) -> Alert:
    now = now or datetime.now(timezone.utc)
    payload = {**payload, "text": text}

    existing = await _find_open(session, car_id=car_id, atype=atype, rule_id=rule_id)
    if existing is not None:
        existing.payload = payload
        existing.last_seen_at = now
        await session.flush()
        return existing

    try:
        async with session.begin_nested():
            alert = Alert(
                rule_id=rule_id,
                car_id=car_id,
                type=atype,
                severity=severity,
                status=AlertStatus.open,
                triggered_at=now,
                last_seen_at=now,
                payload=payload,
            )
            session.add(alert)
            await session.flush()
        return alert
    except IntegrityError:
        # Гонка: другая точка входа уже создала open-алерт. Откатился только
        # SAVEPOINT, изменения остальных машин этого прохода целы.
        existing = await _find_open(session, car_id=car_id, atype=atype, rule_id=rule_id)
        if existing is None:
            raise
        existing.payload = payload
        existing.last_seen_at = now
        await session.flush()
        return existing


async def resolve_open(
    session: AsyncSession,
    *,
    car_id: int,
    atype: AlertType,
    rule_id: int | None = None,
    now: datetime | None = None,
) -> Alert | None:
    """Условие снялось — закрываем алерт сами, без участия админа."""
    alert = await _find_open(session, car_id=car_id, atype=atype, rule_id=rule_id)
    if alert is None:
        return None
    alert.status = AlertStatus.resolved
    alert.resolved_at = now or datetime.now(timezone.utc)
    await session.flush()
    return alert


async def list_alerts(
    session: AsyncSession, *, status: str | None = "open", car_id: int | None = None
) -> list[Alert]:
    query = select(Alert)
    if status:
        try:
            query = query.where(Alert.status == AlertStatus(status))
        except ValueError as exc:
            raise DomainError(f"неизвестный статус алерта: {status}", status_code=422) from exc
    if car_id is not None:
        query = query.where(Alert.car_id == car_id)
    return list(await session.scalars(query.order_by(Alert.triggered_at.desc())))


async def set_status(
    session: AsyncSession, alert: Alert, status: AlertStatus
) -> Alert:
    alert.status = status
    if status is AlertStatus.resolved:
        alert.resolved_at = datetime.now(timezone.utc)
    await session.flush()
    return alert
