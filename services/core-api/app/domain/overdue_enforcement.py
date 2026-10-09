"""Авто-блокировка двигателя за неоплату и авто-разблокировка при оплате.

Отдельная ответственность, вынесенная из `commands.py`: тот отвечает за механику
команд, а здесь — политика «кого глушить за неоплату и когда отпускать».

Системная команда помечается `source=CommandSource.overdue` (колонка
`commands.source`), а не `requested_by IS NULL`: по явной метке авто-разблокировка
снимает ТОЛЬКО свой блок и никогда не трогает ручной блок админа (угон/невозврат).
Активным системный блок считается, только если он — ПОСЛЕДНЯЯ значимая команда
блока/разблокировки машины: более поздний resume или ручной блок его отменяют.

Задачи `block_overdue`/`unblock_paid` — тонкие обёртки над этими функциями.
"""
from __future__ import annotations

import enum
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    AlertType,
    Car,
    CarState,
    Command,
    CommandSource,
    CommandStatus,
    CommandType,
    Driver,
    PaymentSchedule,
)
from app.domain import alerts as alerts_domain
from app.domain import commands as commands_domain
from app.domain import schedules as sched_domain

log = logging.getLogger(__name__)

# Метка системного блока.
SYSTEM = CommandSource.overdue

# Команды блока/разблокировки, которыми определяется «последняя воля» по машине.
_BLOCK_OR_RESUME = (CommandType.engine_stop, CommandType.engine_resume)
# Незначимые статусы: команда не состоялась и на «последнюю волю» не влияет.
_INSIGNIFICANT = (CommandStatus.failed, CommandStatus.blocked_by_safety)

# Пространство имён advisory-lock: страхует от двойного прогона (ручной запуск во
# время cron и т.п.) независимо от concurrency воркера.
_LOCK_NS = 0x0D00


class BlockOutcome(str, enum.Enum):
    armed = "armed"  # взвели системную блокировку
    skipped_blocked = "skipped-blocked"  # уже заблокирована
    skipped_inflight = "skipped-inflight"  # блок уже в полёте (в т.ч. ручной)
    skipped_locked = "skipped-locked"  # машину обрабатывает параллельный прогон
    not_overdue = "not-overdue"  # оплатил, пока шёл цикл — блок не ставим
    no_tracker = "no-tracker"  # к машине не привязан трекер


class ReleaseOutcome(str, enum.Enum):
    no_system_block = "no-system-block"  # системного блока нет (или он снят/ручной)
    still_overdue = "still-overdue"  # долг не закрыт — держим блок
    no_driver = "no-driver"  # нет активного водителя/графика — блок НЕ снимаем
    disarmed = "disarmed"  # сняли взвод, не успевший сработать
    unblock_failed = "unblock-failed"  # resume не ушёл на реле
    unblocked = "unblocked"  # отправили разблокировку
    locked = "locked"  # машину обрабатывает параллельный прогон


async def _acquire_car_lock(session: AsyncSession, car_id: int) -> bool:
    """Транзакционный advisory-lock на машину (освобождается на commit).

    На PostgreSQL не даёт двум параллельным прогонам обрабатывать одну машину
    (SELECT→INSERT дедупа не атомарен). На SQLite (тесты) конкуренции нет — True.
    """
    if session.bind.dialect.name != "postgresql":
        return True
    return bool(
        await session.scalar(select(func.pg_try_advisory_xact_lock(_LOCK_NS, car_id)))
    )


async def overdue_car_ids(session: AsyncSession, now: datetime) -> set[int]:
    """car_id машин, чей водитель просрочен. Единый предикат для обеих задач.

    Активный водитель (`Driver.active`) + активный график (`PaymentSchedule.active`)
    + `schedule_status(...).is_overdue`. Тот же критерий использует и снятие блока,
    иначе блок и разблокировка «мигали» бы друг против друга.
    """
    rows = await session.execute(
        select(Driver.car_id, PaymentSchedule)
        .join(PaymentSchedule, PaymentSchedule.driver_id == Driver.id)
        .where(
            Driver.active.is_(True),
            Driver.car_id.is_not(None),
            PaymentSchedule.active.is_(True),
        )
    )
    return {
        car_id
        for car_id, schedule in rows.all()
        if sched_domain.schedule_status(schedule, now).is_overdue
    }


async def car_payment_overdue(
    session: AsyncSession, car_id: int, now: datetime
) -> bool | None:
    """Просрочен ли водитель машины. None — нет активного водителя/графика.

    None трактуется вызывающим как «неизвестно»: блок в этом случае НЕ снимается,
    иначе отвязка водителя или удаление графика тихо разблокировали бы машину.
    """
    driver = await session.scalar(
        select(Driver)
        .where(Driver.car_id == car_id, Driver.active.is_(True))
        .order_by(Driver.id)  # инвариант: один активный водитель на машину
    )
    if driver is None:
        return None
    schedule = await sched_domain.get_schedule(session, driver.id)
    if schedule is None or not schedule.active:
        return None
    return sched_domain.schedule_status(schedule, now).is_overdue


async def enforce_overdue_block(
    session: AsyncSession, *, car_id: int, now: datetime | None = None
) -> BlockOutcome:
    """Взводит системную блокировку машины за неоплату.

    `always_arm`: блок уйдёт на реле не сразу, а через `fire_armed` на первой
    безопасной остановке — тогда же придут уведомления админу и водителю. Уже
    заблокированную (`engine_blocked`) и машину с блоком «в полёте» пропускаем:
    иначе реле получило бы команду повторно, а админ/водитель — дубль алерта.
    Побочный эффект: создаёт/находит строку `Command`.
    """
    now = now or datetime.now(timezone.utc)
    if not await _acquire_car_lock(session, car_id):
        return BlockOutcome.skipped_locked
    # Кандидаты взяты один раз в начале прогона — перепроверяем под локом: мог
    # оплатить, пока шёл цикл, тогда взводить и уведомлять не нужно.
    if await car_payment_overdue(session, car_id, now) is not True:
        return BlockOutcome.not_overdue
    state = await session.get(CarState, car_id)
    if state is not None and state.engine_blocked:
        return BlockOutcome.skipped_blocked
    # Единственный дедуп — внутри request_command (_recent_duplicate): он вернёт
    # уже существующую in-flight engine_stop (в т.ч. ручную) с DUPLICATE_REASON.
    command, _, reason = await commands_domain.request_command(
        session,
        car_id=car_id,
        type_value="engine_block",
        requested_by=None,
        source=SYSTEM,
        always_arm=True,
        now=now,
    )
    if reason == commands_domain.DUPLICATE_REASON:
        return BlockOutcome.skipped_inflight
    if command.status == CommandStatus.failed:
        return BlockOutcome.no_tracker
    return BlockOutcome.armed


async def _last_significant(session: AsyncSession, car_id: int) -> Command | None:
    """Последняя значимая команда блока/разблокировки машины.

    «Последняя воля»: более поздний `engine_resume` или ручной `engine_stop`
    отменяют прежний системный блок, а несостоявшиеся (`failed`/`blocked_by_safety`)
    не в счёт. По ней же системный блок считается уже снятым после resume — иначе
    старая `acked` жила бы вечно (ложная разблокировка чужого блока, N+1, повторный
    resume при `unconfirmed`).
    """
    return await session.scalar(
        select(Command)
        .where(
            Command.car_id == car_id,
            Command.type.in_(_BLOCK_OR_RESUME),
            Command.status.notin_(_INSIGNIFICANT),
        )
        .order_by(Command.id.desc())
    )


async def _active_system_block(
    session: AsyncSession, car_id: int
) -> Command | None:
    """Активный СИСТЕМНЫЙ блок = последняя значимая команда и это `engine_stop`
    с `source=overdue`. Если позже был resume или ручной блок — вернём None."""
    last = await _last_significant(session, car_id)
    if last is None or last.type != CommandType.engine_stop or last.source != SYSTEM:
        return None
    return last


async def cars_under_system_block(session: AsyncSession) -> list[int]:
    """car_id, у которых ПОСЛЕДНЯЯ значимая команда — системный блок (кандидаты на
    снятие). Resume/ручной блок, пришедшие позже, машину из кандидатов убирают —
    поэтому разблокированные не возвращаются (нет вечных `acked` и N+1)."""
    last_ids = (
        select(Command.car_id, func.max(Command.id).label("mx"))
        .where(
            Command.type.in_(_BLOCK_OR_RESUME),
            Command.status.notin_(_INSIGNIFICANT),
        )
        .group_by(Command.car_id)
        .subquery()
    )
    rows = await session.scalars(
        select(Command.car_id)
        .join(last_ids, Command.id == last_ids.c.mx)
        .where(Command.type == CommandType.engine_stop, Command.source == SYSTEM)
    )
    return list(rows)


async def release_if_paid(
    session: AsyncSession, *, car_id: int, now: datetime | None = None
) -> ReleaseOutcome:
    """Снимает СИСТЕМНУЮ блокировку, если водитель больше не просрочен.

    Решение о снятии держится на `car_payment_overdue`: снимаем только при
    подтверждённом `is_overdue=False`; «нет данных» (нет водителя/графика) — блок
    держим. Взвод, не успевший сработать, снимаем атомарно (гонка с `fire_armed`).
    Реально заглушённую машину разблокируем через `engine_resume` и уведомляем
    (`overdue_unblock`) ТОЛЬКО если команда реально ушла (`sent`/`acked`) — иначе
    не врём «разблокировано», а поднимаем `overdue_unblock_failed` и повторим на
    следующем проходе. При успехе закрываем открытые системные алерты по машине,
    чтобы следующий цикл «блок→оплата» снова доставился (иначе raise_alert
    схлопнул бы новый алерт со старым открытым и бот его не отправил). Ручной блок
    админа не трогаем.
    """
    now = now or datetime.now(timezone.utc)
    if not await _acquire_car_lock(session, car_id):
        return ReleaseOutcome.locked
    block = await _active_system_block(session, car_id)
    if block is None:
        return ReleaseOutcome.no_system_block
    overdue = await car_payment_overdue(session, car_id, now)
    if overdue is None:
        return ReleaseOutcome.no_driver
    if overdue:
        return ReleaseOutcome.still_overdue

    if block.status == CommandStatus.armed:
        # Атомарно: параллельный fire_armed мог захватить armed→queued и отправить.
        # Безусловное присваивание затёрло бы sent/failed, и машина осталась бы
        # заглушённой без авто-снятия. rowcount==0 → пересмотрим на след. проходе.
        res = await session.execute(
            update(Command)
            .where(Command.id == block.id, Command.status == CommandStatus.armed)
            .values(
                status=CommandStatus.failed, result="оплата получена — ожидание снято"
            )
        )
        await session.flush()
        return ReleaseOutcome.disarmed if res.rowcount else ReleaseOutcome.no_system_block

    command, _, _ = await commands_domain.request_command(
        session,
        car_id=car_id,
        type_value="engine_unblock",
        requested_by=None,
        source=SYSTEM,
        now=now,
    )
    car = await session.get(Car, car_id)
    plate = car.plate if car else str(car_id)
    if command.status not in (CommandStatus.sent, CommandStatus.acked):
        # raise_alert схлопывает открытые по типу+машине → доставится один раз,
        # даже если повторяем попытку каждый проход.
        await alerts_domain.raise_alert(
            session,
            car_id=car_id,
            atype=AlertType.overdue_unblock_failed,
            severity="warning",
            payload={"command_id": command.id, "plate": plate, "reason": command.result},
            text=f"{plate}: оплата получена, но авто-разблокировка не прошла — {command.result}",
            now=now,
        )
        return ReleaseOutcome.unblock_failed

    # Условие блокировки снято — закрываем открытые системные алерты по машине,
    # иначе на следующем цикле новый overdue_block_fired схлопнётся со старым
    # открытым (у того notified_at уже стоит) и бот его не доставит.
    await alerts_domain.resolve_open(
        session, car_id=car_id, atype=AlertType.overdue_block_fired, now=now
    )
    await alerts_domain.resolve_open(
        session, car_id=car_id, atype=AlertType.overdue_unblock_failed, now=now
    )
    # И прошлый «разблокировано» закрываем, чтобы ЭТО уведомление об оплате
    # поднялось как новое, а не обновило старое доставленное.
    await alerts_domain.resolve_open(
        session, car_id=car_id, atype=AlertType.overdue_unblock, now=now
    )
    await alerts_domain.raise_alert(
        session,
        car_id=car_id,
        atype=AlertType.overdue_unblock,
        severity="info",
        payload={"command_id": command.id, "plate": plate},
        text=f"{plate}: оплата получена — двигатель разблокирован",
        now=now,
    )
    return ReleaseOutcome.unblocked
