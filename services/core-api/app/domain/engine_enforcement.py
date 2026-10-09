"""Авто-блокировка/разблокировка двигателя по системным политикам.

Механика — взвод, выбор «последней значимой команды», advisory-lock, атомарное
снятие — источник-агностична и параметризуется `source: CommandSource` плюс
политикой (`Policy`): предикат условия + типы/тексты алертов. Политики:
`OVERDUE` (неоплата аренды) и `fines_policy(n)` (неоплаченные штрафы > N).

Системная команда помечается своим `commands.source` (а не `requested_by IS NULL`):
по нему авто-разблокировка снимает ТОЛЬКО блок своего источника и не трогает ни
ручной блок админа (угон), ни блок другого источника. Активным системный блок
считается, только если это ПОСЛЕДНЯЯ значимая команда блока/разблокировки машины:
более поздний resume или ручной блок его отменяют.

Задачи (`payments_block`/`payments_unblock`/`fines_enforcement`) — тонкие обёртки.
"""
from __future__ import annotations

import enum
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Awaitable, Callable

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
from app.domain import fines as fines_domain
from app.domain import schedules as sched_domain

log = logging.getLogger(__name__)

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
    not_applicable = "not-applicable"  # условие больше не выполняется (проверка под локом)
    no_tracker = "no-tracker"  # к машине не привязан трекер


class ReleaseOutcome(str, enum.Enum):
    no_system_block = "no-system-block"  # системного блока нет (или он снят/чужой)
    still_applies = "still-applies"  # условие ещё выполняется — держим блок
    hold = "hold"  # «нет данных» — держим блок как есть
    disarmed = "disarmed"  # сняли взвод, не успевший сработать
    unblock_failed = "unblock-failed"  # resume не ушёл на реле
    unblocked = "unblocked"  # отправили разблокировку
    locked = "locked"  # машину обрабатывает параллельный прогон


# Предикат условия блокировки для машины:
#   True  — выполняется (блокировать / держать блок),
#   False — не выполняется (не блокировать / снять блок),
#   None  — нет данных (не блокировать; при снятии — держать как есть).
Predicate = Callable[[AsyncSession, int, datetime], Awaitable["bool | None"]]


@dataclass(frozen=True)
class Policy:
    """Политика одного источника блокировки."""

    source: CommandSource
    applies: Predicate
    fired_atype: AlertType  # алерт срабатывания (поднимает fire_armed по source)
    unblock_atype: AlertType
    failed_atype: AlertType
    unblock_text: str  # шаблон с {plate}
    failed_text: str  # шаблон с {plate} и {reason}


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
    session: AsyncSession, car_id: int, source: CommandSource
) -> Command | None:
    """Активный системный блок = последняя значимая команда и это `engine_stop`
    нужного `source`. Если позже был resume или ручной/чужой блок — None."""
    last = await _last_significant(session, car_id)
    if last is None or last.type != CommandType.engine_stop or last.source != source:
        return None
    return last


async def cars_under_system_block(
    session: AsyncSession, source: CommandSource
) -> list[int]:
    """car_id, у которых ПОСЛЕДНЯЯ значимая команда — системный блок данного
    источника (кандидаты на снятие). Resume/чужой блок, пришедшие позже, машину из
    кандидатов убирают — разблокированные не возвращаются (нет вечных `acked`, N+1)."""
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
        .where(Command.type == CommandType.engine_stop, Command.source == source)
    )
    return list(rows)


async def enforce_block(
    session: AsyncSession, *, car_id: int, policy: Policy, now: datetime | None = None
) -> BlockOutcome:
    """Взводит системную блокировку машины для политики `policy`.

    Блок уйдёт на реле не сразу, а через `fire_armed` (`always_arm`) на первой
    безопасной остановке — тогда же придут уведомления. Кандидаты берутся в начале
    прогона — под локом перепроверяем условие (`policy.applies`): мог измениться,
    пока шёл цикл. Уже заблокированную и машину с блоком «в полёте» пропускаем.
    """
    now = now or datetime.now(timezone.utc)
    if not await _acquire_car_lock(session, car_id):
        return BlockOutcome.skipped_locked
    if await policy.applies(session, car_id, now) is not True:
        return BlockOutcome.not_applicable
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
        source=policy.source,
        always_arm=True,
        now=now,
    )
    if reason == commands_domain.DUPLICATE_REASON:
        return BlockOutcome.skipped_inflight
    if command.status == CommandStatus.failed:
        return BlockOutcome.no_tracker
    return BlockOutcome.armed


async def release_if_cleared(
    session: AsyncSession, *, car_id: int, policy: Policy, now: datetime | None = None
) -> ReleaseOutcome:
    """Снимает системный блок политики, если её условие больше не выполняется.

    `policy.applies`: `True` — держим блок; `None` — «нет данных», тоже держим
    (иначе пропажа данных тихо разблокировала бы); `False` — снимаем. Взвод, не
    успевший сработать, снимаем атомарно (гонка с `fire_armed`). Реально заглушённую
    машину разблокируем через `engine_resume` и уведомляем ТОЛЬКО если команда
    реально ушла (`sent`/`acked`); иначе поднимаем `failed_atype` и повторим на
    следующем проходе. При успехе закрываем открытые алерты этого источника, чтобы
    следующий цикл снова доставился. Чужой/ручной блок не трогаем.
    """
    now = now or datetime.now(timezone.utc)
    if not await _acquire_car_lock(session, car_id):
        return ReleaseOutcome.locked
    block = await _active_system_block(session, car_id, policy.source)
    if block is None:
        return ReleaseOutcome.no_system_block
    verdict = await policy.applies(session, car_id, now)
    if verdict is True:
        return ReleaseOutcome.still_applies
    if verdict is None:
        return ReleaseOutcome.hold

    if block.status == CommandStatus.armed:
        # Атомарно: параллельный fire_armed мог захватить armed→queued и отправить.
        # Безусловное присваивание затёрло бы sent/failed. rowcount==0 → пересмотрим.
        res = await session.execute(
            update(Command)
            .where(Command.id == block.id, Command.status == CommandStatus.armed)
            .values(status=CommandStatus.failed, result="условие снято — ожидание отменено")
        )
        await session.flush()
        return ReleaseOutcome.disarmed if res.rowcount else ReleaseOutcome.no_system_block

    command, _, _ = await commands_domain.request_command(
        session,
        car_id=car_id,
        type_value="engine_unblock",
        requested_by=None,
        source=policy.source,
        now=now,
    )
    car = await session.get(Car, car_id)
    plate = car.plate if car else str(car_id)
    if command.status not in (CommandStatus.sent, CommandStatus.acked):
        await alerts_domain.raise_alert(
            session,
            car_id=car_id,
            atype=policy.failed_atype,
            severity="warning",
            payload={"command_id": command.id, "plate": plate, "reason": command.result},
            text=policy.failed_text.format(plate=plate, reason=command.result),
            now=now,
        )
        return ReleaseOutcome.unblock_failed

    # Условие снято — закрываем открытые алерты источника, иначе на следующем цикле
    # новый *_block_fired схлопнётся со старым открытым (у того notified_at стоит).
    for atype in (policy.fired_atype, policy.failed_atype, policy.unblock_atype):
        await alerts_domain.resolve_open(session, car_id=car_id, atype=atype, now=now)
    await alerts_domain.raise_alert(
        session,
        car_id=car_id,
        atype=policy.unblock_atype,
        severity="info",
        payload={"command_id": command.id, "plate": plate},
        text=policy.unblock_text.format(plate=plate),
        now=now,
    )
    return ReleaseOutcome.unblocked


# --- политика «неоплата аренды» ---------------------------------------------


async def overdue_car_ids(session: AsyncSession, now: datetime) -> set[int]:
    """car_id машин, чей водитель просрочен (кандидаты блока за аренду).

    Активный водитель + активный график + `schedule_status(...).is_overdue`.
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
    """Просрочен ли водитель машины. None — нет активного водителя/графика."""
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


OVERDUE = Policy(
    source=CommandSource.overdue,
    applies=car_payment_overdue,
    fired_atype=AlertType.overdue_block_fired,
    unblock_atype=AlertType.overdue_unblock,
    failed_atype=AlertType.overdue_unblock_failed,
    unblock_text="{plate}: оплата получена — двигатель разблокирован",
    failed_text="{plate}: оплата получена, но авто-разблокировка не прошла — {reason}",
)


async def enforce_overdue_block(
    session: AsyncSession, *, car_id: int, now: datetime | None = None
) -> BlockOutcome:
    return await enforce_block(session, car_id=car_id, policy=OVERDUE, now=now)


async def release_if_paid(
    session: AsyncSession, *, car_id: int, now: datetime | None = None
) -> ReleaseOutcome:
    return await release_if_cleared(session, car_id=car_id, policy=OVERDUE, now=now)


# --- политика «неоплаченные штрафы > N» -------------------------------------


def fines_policy(threshold: int) -> Policy:
    """Политика блока по числу неоплаченных штрафов: блокируем при строго > N."""

    async def applies(session: AsyncSession, car_id: int, now: datetime) -> bool:
        return (await fines_domain.count_unpaid(session, car_id)) > threshold

    return Policy(
        source=CommandSource.fines,
        applies=applies,
        fired_atype=AlertType.fines_block_fired,
        unblock_atype=AlertType.fines_unblock,
        failed_atype=AlertType.fines_unblock_failed,
        unblock_text="{plate}: штрафы погашены — двигатель разблокирован",
        failed_text="{plate}: штрафы погашены, но авто-разблокировка не прошла — {reason}",
    )
