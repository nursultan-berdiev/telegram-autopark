"""Команды на трекер: защитный гейт, идемпотентность, аудит, подтверждение.

Блокировка едущей машины опаснее неблокировки, поэтому гейт живёт здесь,
в домене, а не в адаптере (plan/06).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.adapter import AdapterError, send_command
from app.config import settings
from app.db.models import (
    Alert,
    AlertType,
    Car,
    CarState,
    Command,
    CommandStatus,
    CommandType,
    Tracker,
)
from app.domain import alerts as alerts_domain
from app.domain import telemetry as telemetry_domain

log = logging.getLogger(__name__)

# Стоящая машина шумит по GPS: строгое == 0 не выполнилось бы никогда.
STOPPED_SPEED_KNOTS = 1.0

BLOCK_TYPES = {CommandType.engine_stop}

_API_TO_COMMAND = {
    "engine_block": CommandType.engine_stop,
    "engine_stop": CommandType.engine_stop,
    "engine_unblock": CommandType.engine_resume,
    "engine_resume": CommandType.engine_resume,
    "alarm_arm": CommandType.alarm_arm,
    "alarm_disarm": CommandType.alarm_disarm,
}


@dataclass
class GateResult:
    passed: bool
    reason: str | None
    snapshot: dict


def parse_type(value: str) -> CommandType:
    try:
        return _API_TO_COMMAND[value]
    except KeyError as exc:
        raise ValueError(f"неизвестная команда: {value}") from exc


def check_safety(state: CarState | None, now: datetime | None = None) -> GateResult:
    """Блокируем только стоящую машину с выключенным зажиганием и живой связью."""
    now = now or datetime.now(timezone.utc)
    online = telemetry_domain.is_online(state, now)
    speed = float(state.speed_knots) if state and state.speed_knots is not None else None
    snapshot = {
        "online": online,
        "speed_knots": speed,
        "ignition": state.ignition if state else None,
        "motion": state.motion if state else None,
        "last_ts": state.last_ts.isoformat() if state and state.last_ts else None,
        "checked_at": now.isoformat(),
    }

    if not online:
        return GateResult(False, "нет свежей телеметрии — машина не на связи", snapshot)
    if speed is None or state is None or state.motion is None or state.ignition is None:
        # Неполный кадр телеметрии: неизвестно ≠ безопасно.
        return GateResult(False, "неполные данные о машине — блокировать вслепую нельзя", snapshot)
    if speed >= STOPPED_SPEED_KNOTS:
        return GateResult(False, "машина в движении", snapshot)
    if state.motion:
        return GateResult(False, "машина в движении", snapshot)
    if state.ignition:
        return GateResult(False, "зажигание включено", snapshot)
    return GateResult(True, None, snapshot)


async def _recent_duplicate(
    session: AsyncSession,
    *,
    car_id: int,
    ctype: CommandType,
    alert_id: int | None,
    now: datetime,
) -> Command | None:
    """Двойной тап не должен слать вторую команду на реле."""
    edge = now - timedelta(seconds=settings.command_dedup_seconds)
    query = select(Command).where(
        Command.car_id == car_id,
        Command.type == ctype,
        Command.created_at.is_not(None),
    )
    if alert_id is not None:
        query = query.where(Command.alert_id == alert_id)
    query = query.order_by(Command.id.desc())
    for command in await session.scalars(query):
        created = command.created_at
        created = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        fresh = created >= edge
        # acked/failed/unconfirmed — терминальные: повторная команда законна.
        in_flight = command.status in (CommandStatus.queued, CommandStatus.sent)
        if in_flight or (fresh and command.status is CommandStatus.acked):
            return command
    return None


async def request_command(
    session: AsyncSession,
    *,
    car_id: int,
    type_value: str,
    requested_by: int | None,
    alert_id: int | None = None,
    now: datetime | None = None,
) -> tuple[Command, bool, str | None]:
    """Возвращает (команда, отправлена ли, причина отказа)."""
    now = now or datetime.now(timezone.utc)
    ctype = parse_type(type_value)

    duplicate = await _recent_duplicate(
        session, car_id=car_id, ctype=ctype, alert_id=alert_id, now=now
    )
    if duplicate is not None:
        return duplicate, duplicate.status == CommandStatus.sent, "команда уже отправлена"

    tracker = await session.scalar(
        select(Tracker).where(Tracker.car_id == car_id, Tracker.active.is_(True))
    )
    command = Command(
        car_id=car_id,
        tracker_id=tracker.id if tracker else None,
        type=ctype,
        status=CommandStatus.queued,
        requested_by=requested_by,
        alert_id=alert_id,
    )
    session.add(command)

    if tracker is None:
        command.status = CommandStatus.failed
        command.result = "трекер не привязан к машине"
        await session.flush()
        return command, False, command.result

    state = await session.get(CarState, car_id)
    if ctype in BLOCK_TYPES:
        gate = check_safety(state, now)
        command.safety_snapshot = gate.snapshot
        if not gate.passed:
            # Отказ гейта — не ошибка: это штатный статус с понятной причиной.
            command.status = CommandStatus.blocked_by_safety
            command.result = gate.reason
            await session.flush()
            return command, False, gate.reason

    try:
        response = await send_command(tracker.external_id, ctype.value)
    except AdapterError as exc:
        command.status = CommandStatus.failed
        command.result = str(exc)
        await session.flush()
        return command, False, command.result

    command.status = (
        CommandStatus.sent if response.get("status") == "sent" else CommandStatus.failed
    )
    command.result = str(response.get("result") or "")[:2000]

    if state is not None:
        state.last_command = ctype.value

    if alert_id is not None and command.status == CommandStatus.sent:
        alert = await session.get(Alert, alert_id)
        if alert is not None:
            alert.action_taken = (
                "engine_block" if ctype is CommandType.engine_stop else ctype.value
            )

    await session.flush()
    ok = command.status == CommandStatus.sent
    return command, ok, None if ok else command.result


CONFIRMABLE_TYPES = (CommandType.engine_stop, CommandType.engine_resume)


async def confirm_by_telemetry(
    session: AsyncSession,
    *,
    car_ids: list[int] | None = None,
    now: datetime | None = None,
) -> int:
    """Подтверждение приходит битом 27, а не ответом трекера.

    Команды сигнализации подтвердить нечем — телеметрия про них молчит,
    поэтому их сюда не берём (иначе «подтвердились» бы сами собой).
    """
    now = now or datetime.now(timezone.utc)
    query = select(Command).where(
        Command.status == CommandStatus.sent, Command.type.in_(CONFIRMABLE_TYPES)
    )
    if car_ids:
        query = query.where(Command.car_id.in_(car_ids))
    pending = list(await session.scalars(query))
    confirmed = 0
    for command in pending:
        state = await session.get(CarState, command.car_id)
        if state is None or state.last_ts is None:
            continue
        # Подтверждать можно только точкой, пришедшей ПОСЛЕ отправки команды:
        # иначе старый снимок «подтвердит» блокировку, которой не было.
        last_ts = state.last_ts
        last_ts = last_ts if last_ts.tzinfo else last_ts.replace(tzinfo=timezone.utc)
        created = command.created_at
        created = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        if last_ts < created:
            continue
        # За окном подтверждения команду ведёт джоба досрочивания.
        if (now - created).total_seconds() > settings.command_ack_window_seconds:
            continue
        blocked = bool(state.engine_blocked)
        expected = command.type is CommandType.engine_stop
        if blocked == expected:
            command.status = CommandStatus.acked
            command.acked_at = now
            confirmed += 1
    if confirmed:
        await session.commit()
    return confirmed


async def sweep_unconfirmed(
    session: AsyncSession, *, now: datetime | None = None
) -> int:
    """Если трекер замолчал, подтверждение не придёт никогда — закрываем сами.

    Иначе команда осталась бы «отправленной» навсегда, а админ не узнал бы,
    что блокировка не подтвердилась (возможно, нет реле).
    """
    now = now or datetime.now(timezone.utc)
    edge = now - timedelta(seconds=settings.command_ack_window_seconds)
    stale = list(
        await session.scalars(
            select(Command).where(
                Command.status == CommandStatus.sent,
                Command.type.in_(CONFIRMABLE_TYPES),
            )
        )
    )
    count = 0
    for command in stale:
        created = command.created_at
        created = created if created.tzinfo else created.replace(tzinfo=timezone.utc)
        if created > edge:
            continue
        command.status = CommandStatus.unconfirmed
        car = await session.get(Car, command.car_id)
        plate = car.plate if car else str(command.car_id)
        await alerts_domain.raise_alert(
            session,
            car_id=command.car_id,
            atype=AlertType.command_unconfirmed,
            severity="warning",
            payload={
                "command_id": command.id,
                "command_type": command.type.value,
                "plate": plate,
            },
            text=(
                f"команда {command.type.value} ушла, но блокировка не подтверждена "
                "телеметрией — возможно, нет реле"
            ),
            now=now,
        )
        count += 1
    if count:
        await session.commit()
    return count


async def list_commands(session: AsyncSession, car_id: int) -> list[Command]:
    return list(
        await session.scalars(
            select(Command)
            .where(Command.car_id == car_id)
            .order_by(Command.id.desc())
        )
    )
