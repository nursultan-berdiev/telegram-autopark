"""Команды на трекер: защитный гейт, идемпотентность, аудит, подтверждение.

Блокировка едущей машины опаснее неблокировки, поэтому гейт живёт здесь,
в домене, а не в адаптере (plan/06).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.clients.adapter import AdapterError, send_command
from app.config import settings
from app.db.models import (
    Alert,
    AlertType,
    Car,
    CarState,
    Command,
    CommandSource,
    CommandStatus,
    CommandType,
    Tracker,
)
from app.domain import alerts as alerts_domain
from app.domain import telemetry as telemetry_domain

log = logging.getLogger(__name__)

# Причина отказа при дедупе — одна на весь модуль: по ней вызывающий (в т.ч.
# авто-блокировка) отличает «уже в полёте» от фактической отправки.
DUPLICATE_REASON = "команда уже отправлена"

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
        # armed — ещё в ожидании: повторный взвод должен вернуть ту же команду.
        in_flight = command.status in (
            CommandStatus.queued,
            CommandStatus.sent,
            CommandStatus.armed,
        )
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
    arm_if_unsafe: bool = False,
    always_arm: bool = False,
    source: CommandSource = CommandSource.manual,
    now: datetime | None = None,
) -> tuple[Command, bool, str | None]:
    """Возвращает (команда, отправлена ли, причина отказа).

    `arm_if_unsafe`: если гейт не пропускает (машина едет/офлайн), не отказываем,
    а взводим блокировку (`armed`) — отправится сама, когда машина встанет.

    `always_arm`: взвести блокировку ВСЕГДА, даже на уже стоящей машине (не слать
    сразу). Нужно авто-блокировке за неоплату: так и стоящая машина глохнет не
    молча, а через `fire_armed` с уведомлением админа и водителя. Фактическую
    отправку на реле всё равно решает тот же гейт.

    `source`: кто инициировал (`manual` — админ, `overdue` — авто-блокировка за
    неоплату). Хранится явной колонкой: по ней авто-разблокировка отличает свой
    блок от ручного и никогда не снимает ручной (защита от снятия при угоне).
    """
    now = now or datetime.now(timezone.utc)
    ctype = parse_type(type_value)

    # Разблокировка снимает взведённую блокировку этой машины: иначе engine_resume
    # (например, со старой карточки алерта) не трогает armed engine_stop, и машина
    # заглушится на остановке вопреки последней воле админа. Системный resume
    # (source=overdue, из release_if_paid) снимает ТОЛЬКО свои взводы — ручной
    # отложенный блок админа (угон: «заглушить на остановке») не трогаем.
    if ctype is CommandType.engine_resume:
        stmt = update(Command).where(
            Command.car_id == car_id,
            Command.type == CommandType.engine_stop,
            Command.status == CommandStatus.armed,
        )
        if source != CommandSource.manual:
            stmt = stmt.where(Command.source == source)
        await session.execute(
            stmt.values(status=CommandStatus.failed, result="снята разблокировкой двигателя")
        )

    # Ручная блокировка «забирает» себе активные системные команды машины (взвод
    # или блок за неоплату): так авто-разблокировка после оплаты их уже не снимет.
    # Делаем это ЯВНО (а не только через дедуп), иначе блок с карточки алерта
    # (свой alert_id) дедуп системного не находит и защита зависела бы от пути.
    if ctype is CommandType.engine_stop and source == CommandSource.manual:
        await session.execute(
            update(Command)
            .where(
                Command.car_id == car_id,
                Command.type == CommandType.engine_stop,
                Command.source == CommandSource.overdue,
                Command.status.in_(
                    (
                        CommandStatus.armed,
                        CommandStatus.queued,
                        CommandStatus.sent,
                        CommandStatus.acked,
                        CommandStatus.unconfirmed,
                    )
                ),
            )
            .values(source=CommandSource.manual)
        )

    duplicate = await _recent_duplicate(
        session, car_id=car_id, ctype=ctype, alert_id=alert_id, now=now
    )
    if duplicate is not None:
        return duplicate, duplicate.status == CommandStatus.sent, DUPLICATE_REASON

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
        source=source,
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
        if always_arm:
            # Взводим независимо от гейта: отправит и уведомит fire_armed на
            # первой безопасной точке (стоящая машина — уже следующая точка).
            command.status = CommandStatus.armed
            command.result = gate.reason or "взведено автоматически (неоплата)"
            await session.flush()
            return command, False, command.result
        if not gate.passed:
            if arm_if_unsafe:
                # Не отказываем — взводим: заглушим сами, как только машина встанет.
                command.status = CommandStatus.armed
                command.result = gate.reason
                await session.flush()
                return command, False, gate.reason
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

    # Застрявший захват (`queued`) после сбоя/рестарта в окне отправки: возвращаем
    # в `armed`, чтобы взвод дожил до следующей точки и не блокировал новые команды
    # через _recent_duplicate. Персистентный `queued` бывает только от fire_armed —
    # request_command до коммита всегда уводит команду из `queued`.
    recovered = 0
    stuck = list(
        await session.scalars(
            select(Command).where(
                Command.status == CommandStatus.queued, Command.type.in_(BLOCK_TYPES)
            )
        )
    )
    for command in stuck:
        # Возраст ЗАХВАТА, не создания: created_at у взвода без TTL всегда старый,
        # а updated_at выставлен моментом захвата (armed -> queued).
        claimed = command.updated_at
        if claimed is None:
            continue
        claimed = claimed if claimed.tzinfo else claimed.replace(tzinfo=timezone.utc)
        if claimed > edge:
            continue  # захват свежий — отправка, возможно, ещё идёт; не трогаем
        command.status = CommandStatus.armed
        command.result = "захват завис (сбой/рестарт) — возвращено в ожидание"
        recovered += 1

    if count or recovered:
        await session.commit()
    return count


async def _raise_armed_failed(
    session: AsyncSession, command: Command, now: datetime
) -> None:
    """Терминальный провал взвода — поднимаем алерт, чтобы админ не остался в
    неведении (для угона/невозврата это самый неприятный исход)."""
    car = await session.get(Car, command.car_id)
    plate = car.plate if car else str(command.car_id)
    await alerts_domain.raise_alert(
        session,
        car_id=command.car_id,
        atype=AlertType.armed_block_failed,
        severity="warning",
        payload={"command_id": command.id, "plate": plate, "reason": command.result},
        text=f"{plate}: отложенная блокировка не сработала — {command.result}",
        now=now,
    )


async def fire_armed(
    session: AsyncSession,
    *,
    car_ids: list[int] | None = None,
    now: datetime | None = None,
) -> int:
    """Отправляет взведённые блокировки на реле, как только машина встала.

    Зовётся на каждом батче телеметрии: машина только что прислала свежую точку,
    значит онлайн и известны скорость/зажигание/движение. Пока гейт не пройден —
    команда остаётся `armed` (срока нет: ждём, пока админ не отменит).

    Перед сетевым вызовом команду атомарно «захватываем» (armed → queued с
    коммитом): второй параллельный батч и отмена увидят уже не `armed` и
    пройдут мимо — иначе реле получило бы команду дважды, а отмена во время
    отправки была бы проигнорирована. Временный сбой адаптера возвращает команду
    в `armed` (повтор на следующей точке); терминальный провал поднимает алерт.
    Возвращает число фактически отправленных команд.
    """
    now = now or datetime.now(timezone.utc)
    query = select(Command.id).where(
        Command.status == CommandStatus.armed, Command.type.in_(BLOCK_TYPES)
    )
    if car_ids:
        query = query.where(Command.car_id.in_(car_ids))
    candidate_ids = list(await session.scalars(query))

    fired = 0
    for cmd_id in candidate_ids:
        command = await session.get(Command, cmd_id)
        if command is None or command.status != CommandStatus.armed:
            continue
        state = await session.get(CarState, command.car_id)
        gate = check_safety(state, now)
        if not gate.passed:
            continue  # ещё не встала — ждём следующую точку, команда остаётся armed

        # Атомарный захват: armed -> queued. Кто не успел (другой батч/отмена) —
        # получит rowcount 0 и пройдёт мимо.
        claim = await session.execute(
            update(Command)
            .where(Command.id == cmd_id, Command.status == CommandStatus.armed)
            # updated_at = момент захвата: по нему sweep отличает идущую отправку
            # от зависшей (created_at у взвода без TTL всегда старый).
            .values(status=CommandStatus.queued, updated_at=func.now())
        )
        if claim.rowcount == 0:
            await session.rollback()
            continue
        await session.commit()  # захват виден другим ДО сетевого вызова

        command = await session.get(Command, cmd_id)
        command.safety_snapshot = gate.snapshot
        tracker = await session.scalar(
            select(Tracker).where(
                Tracker.car_id == command.car_id, Tracker.active.is_(True)
            )
        )
        if tracker is None:
            command.status = CommandStatus.failed
            command.result = "трекер не привязан к машине"
            await _raise_armed_failed(session, command, now)
            await session.commit()
            continue

        # Любой сбой отправки/фиксации возвращает захваченную команду в `armed`,
        # иначе она навсегда зависла бы в `queued`: не AdapterError (напр. не-JSON
        # ответ → ValueError в adapter.py), падение raise_alert, рестарт core-api.
        # Застрявший `queued` не подхватывает ни этот метод, ни sweep, а
        # _recent_duplicate считал бы его «в полёте» и блокировал новые команды.
        try:
            response = await send_command(tracker.external_id, command.type.value)
            if response.get("status") == "sent":
                command.status = CommandStatus.sent
                command.result = str(response.get("result") or "")[:2000]
                # Окно подтверждения (confirm_by_telemetry/sweep_unconfirmed) мерят
                # от created_at. У взвода он — момент нажатия (может быть давно),
                # поэтому переносим его на момент отправки; исходный момент виден в
                # алерте armed_block_fired. Иначе сработавший взвод никогда бы не
                # подтвердился и ловил ложный command_unconfirmed.
                command.created_at = now
                state = await session.get(CarState, command.car_id)
                if state is not None:
                    state.last_command = command.type.value
                car = await session.get(Car, command.car_id)
                plate = car.plate if car else str(command.car_id)
                # Системный взвод (source=overdue) ставит планировщик за неоплату —
                # у него свой тип алерта и платёжный текст водителю.
                if command.source == CommandSource.overdue:
                    atype = AlertType.overdue_block_fired
                    text = (
                        f"{plate}: оплата не поступила к сроку — "
                        "двигатель заблокирован"
                    )
                else:
                    atype = AlertType.armed_block_fired
                    text = (
                        f"{plate} встала — двигатель заблокирован "
                        "(сработала отложенная блокировка)"
                    )
                await alerts_domain.raise_alert(
                    session,
                    car_id=command.car_id,
                    atype=atype,
                    severity="warning",
                    payload={"command_id": command.id, "plate": plate},
                    text=text,
                    now=now,
                )
                fired += 1
            else:
                command.status = CommandStatus.failed
                command.result = str(response.get("result") or "адаптер не принял команду")[:2000]
                await _raise_armed_failed(session, command, now)
            await session.commit()
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            stuck = await session.get(Command, cmd_id)
            if stuck is not None and stuck.status is CommandStatus.queued:
                stuck.status = CommandStatus.armed
                stuck.result = f"сбой отправки, повтор на следующей точке: {exc}"[:2000]
                await session.commit()

    return fired


async def cancel_armed(
    session: AsyncSession, *, car_id: int, command_id: int
) -> tuple[Command | None, bool, str | None]:
    """Снимает взведённую блокировку, пока она не сработала."""
    command = await session.get(Command, command_id)
    if command is None or command.car_id != car_id:
        return None, False, "команда не найдена"
    if command.status != CommandStatus.armed:
        return command, False, "блокировка уже не в ожидании"
    command.status = CommandStatus.failed
    command.result = "ожидание отменено администратором"
    await session.flush()
    return command, True, None


async def list_commands(session: AsyncSession, car_id: int) -> list[Command]:
    return list(
        await session.scalars(
            select(Command)
            .where(Command.car_id == car_id)
            .order_by(Command.id.desc())
        )
    )
