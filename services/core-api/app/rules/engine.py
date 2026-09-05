"""Движок правил: обход включённых правил и ведение алертов."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AlertType,
    Car,
    CarState,
    MaintenanceItem,
    Rule,
    RuleType,
)
from app.domain import alerts as alerts_domain
from app.rules.evaluators import EVALUATORS

log = logging.getLogger(__name__)


async def _cars_for_rule(session: AsyncSession, rule: Rule) -> list[Car]:
    if rule.car_id is not None:
        car = await session.get(Car, rule.car_id)
        return [car] if car is not None else []
    return list(await session.scalars(select(Car)))


async def evaluate_rule(
    session: AsyncSession,
    rule: Rule,
    *,
    car_ids: list[int] | None = None,
    now: datetime | None = None,
) -> int:
    now = now or datetime.now(timezone.utc)
    evaluator = EVALUATORS[rule.type.value]
    triggered = 0

    for car in await _cars_for_rule(session, rule):
        if car_ids and car.id not in car_ids:
            continue
        hit = await evaluator(session, car, rule.params or {}, now)
        if hit.triggered:
            await alerts_domain.raise_alert(
                session,
                car_id=car.id,
                atype=AlertType(rule.type.value),
                severity=rule.severity,
                payload={**hit.payload, "plate": car.plate},
                text=hit.human,
                rule_id=rule.id,
                now=now,
            )
            triggered += 1
        else:
            await alerts_domain.resolve_open(
                session,
                car_id=car.id,
                atype=AlertType(rule.type.value),
                rule_id=rule.id,
                now=now,
            )
    return triggered


async def evaluate_all(session: AsyncSession, *, now: datetime | None = None) -> int:
    now = now or datetime.now(timezone.utc)
    rules = list(await session.scalars(select(Rule).where(Rule.enabled.is_(True))))
    total = 0
    for rule in rules:
        total += await evaluate_rule(session, rule, now=now)
    await check_odometer_trust(session, now=now)
    await session.commit()
    return total


async def check_odometer_trust(
    session: AsyncSession, *, car_ids: list[int] | None = None, now: datetime | None = None
) -> int:
    """Молчаливый пропуск ТО — скрытая потеря функции, поэтому поднимаем алерт.

    Только для машин, где ТО реально ведётся: иначе это был бы шум.
    """
    now = now or datetime.now(timezone.utc)
    raised = 0
    query = select(CarState)
    if car_ids:
        query = query.where(CarState.car_id.in_(car_ids))
    states = list(await session.scalars(query))
    for state in states:
        items = list(
            await session.scalars(
                select(MaintenanceItem).where(MaintenanceItem.car_id == state.car_id)
            )
        )
        if not items:
            continue
        mismatched = any(
            item.last_service_tracker_id != state.odometer_tracker_id for item in items
        )
        untrusted = (not state.odometer_trusted) or mismatched
        car = await session.get(Car, state.car_id)
        plate = car.plate if car else str(state.car_id)
        if untrusted:
            await alerts_domain.raise_alert(
                session,
                car_id=state.car_id,
                atype=AlertType.odometer_untrusted,
                severity="info",
                payload={
                    "plate": plate,
                    "odometer_tracker_id": state.odometer_tracker_id,
                    "odometer_trusted": state.odometer_trusted,
                },
                text=f"по машине {plate} требуется переустановка базы пробега",
                now=now,
            )
            raised += 1
        else:
            await alerts_domain.resolve_open(
                session,
                car_id=state.car_id,
                atype=AlertType.odometer_untrusted,
                now=now,
            )
    return raised


async def evaluate_after_telemetry(
    session: AsyncSession, *, car_ids: list[int] | None = None, now: datetime | None = None
) -> int:
    """Пробег меняется чаще таймера — реагируем сразу на приход телеметрии.

    Считаем только машины из батча: иначе одна точка тянула бы полный обход парка.
    """
    now = now or datetime.now(timezone.utc)
    rules = list(
        await session.scalars(
            select(Rule).where(
                Rule.enabled.is_(True), Rule.type == RuleType.maintenance_km
            )
        )
    )
    total = 0
    for rule in rules:
        if car_ids and rule.car_id is not None and rule.car_id not in car_ids:
            continue
        total += await evaluate_rule(session, rule, car_ids=car_ids, now=now)
    await check_odometer_trust(session, car_ids=car_ids, now=now)
    await session.commit()
    return total
