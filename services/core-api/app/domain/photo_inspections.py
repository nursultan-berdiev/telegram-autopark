"""Фотоконтроль состояния машины: срок, сдача осмотра, просрочка, напоминания.

Срок — локальная дата парка (`photo_schedules.next_due_on`): сдать до конца дня.
Следующий срок = день сдачи + PHOTO_INSPECTION_INTERVAL_DAYS. Водителю без графика
(запуск фичи, наём) срок ставится на завтра: так первое напоминание всегда
приходит раньше алерта админу.

`car_overdue_days` — единая мера просрочки: по ней идут напоминания и алерт админу,
и её же берёт политика авто-блокировки (`engine_enforcement.photo_policy`).
"""
from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import escape
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.db.models import (
    Alert,
    AlertStatus,
    AlertType,
    Car,
    Driver,
    PhotoInspection,
    PhotoSchedule,
)
from app.domain import alerts as alerts_domain
from app.domain import outbox
from app.errors import Conflict, DomainError, Validation

log = logging.getLogger(__name__)

REMINDER_KIND = "photo_reminder"  # outbound_messages.kind
# Та же четвёрка фото в этом окне — повтор после таймаута, а не новая сдача.
REPEAT_WINDOW = timedelta(minutes=10)
# Пространство имён advisory-lock сдачи (у engine_enforcement — 0x0D00).
_SUBMIT_LOCK_NS = 0x0D01
_HOW = "Нажмите «📸 Фотоконтроль» и пришлите 4 фото: спереди, сзади, салон спереди, салон сзади."


def local_today(now: datetime) -> date:
    """Дата по часам парка: срок фотоконтроля — локальный день, а не UTC."""
    return now.astimezone(ZoneInfo(settings.timezone)).date()


def overdue_days(next_due_on: date, today: date) -> int:
    """Срок сегодня — ещё не просрочка: она начинается со следующего дня."""
    return max(0, (today - next_due_on).days)


@dataclass
class PhotoStatus:
    driver_id: int
    driver_name: str
    tg_user_id: int
    car_id: int
    car_plate: str
    next_due_on: date
    overdue_days: int
    last_inspection_id: int | None
    last_inspection_at: datetime | None


@dataclass
class InspectionView:
    """Осмотр с подписью для админа: кто сдал и по какой машине."""

    inspection: PhotoInspection
    driver_name: str | None
    car_plate: str | None  # None — машину удалили (car_id SET NULL)


async def _schedule(session: AsyncSession, driver_id: int) -> PhotoSchedule | None:
    return await session.scalar(select(PhotoSchedule).where(PhotoSchedule.driver_id == driver_id))


async def _ensure_schedule(session: AsyncSession, driver_id: int, *, due: date) -> PhotoSchedule:
    """График водителя; нет — заводит со сроком `due`.

    Заводят его три входа (сдача, наём, ежедневный проход). Встречная вставка
    упрётся в unique(driver_id) — откатываем только SAVEPOINT и берём чужую строку.
    """
    schedule = await _schedule(session, driver_id)
    if schedule is not None:
        return schedule
    try:
        async with session.begin_nested():
            schedule = PhotoSchedule(driver_id=driver_id, next_due_on=due)
            session.add(schedule)
    except IntegrityError:
        schedule = await _schedule(session, driver_id)
        if schedule is None:
            raise
    return schedule


async def restart_schedule(session: AsyncSession, driver_id: int, today: date) -> PhotoSchedule:
    """Наём или повторный наём: отсчёт заново, первый срок — завтра."""
    tomorrow = today + timedelta(days=1)
    schedule = await _ensure_schedule(session, driver_id, due=tomorrow)
    schedule.next_due_on = tomorrow
    schedule.last_reminded_on = None
    await session.flush()
    return schedule


async def _active_drivers_with_car(
    session: AsyncSession, driver_id: int | None = None
) -> list[tuple[Driver, Car]]:
    query = (
        select(Driver, Car)
        .join(Car, Car.id == Driver.car_id)
        .where(Driver.active.is_(True))
        .order_by(Car.plate)
    )
    if driver_id is not None:
        query = query.where(Driver.id == driver_id)
    return [(d, c) for d, c in (await session.execute(query)).all()]


async def _schedules_of(session: AsyncSession, ids: list[int]) -> dict[int, PhotoSchedule]:
    """Графики водителей по driver_id — одним запросом."""
    if not ids:
        return {}
    rows = await session.scalars(select(PhotoSchedule).where(PhotoSchedule.driver_id.in_(ids)))
    return {s.driver_id: s for s in rows}


async def _last_inspections(session: AsyncSession, ids: list[int]) -> dict[int, PhotoInspection]:
    """Последний осмотр каждого из водителей — одним запросом."""
    if not ids:
        return {}
    last_ids = (
        select(func.max(PhotoInspection.id))
        .where(PhotoInspection.driver_id.in_(ids))
        .group_by(PhotoInspection.driver_id)
    )
    rows = await session.scalars(select(PhotoInspection).where(PhotoInspection.id.in_(last_ids)))
    return {i.driver_id: i for i in rows}


def _status(
    driver: Driver, car: Car, due: date, last: PhotoInspection | None, today: date
) -> PhotoStatus:
    return PhotoStatus(
        driver_id=driver.id,
        driver_name=driver.full_name,
        tg_user_id=driver.tg_user_id,
        car_id=car.id,
        car_plate=car.plate,
        next_due_on=due,
        overdue_days=overdue_days(due, today),
        last_inspection_id=last.id if last else None,
        last_inspection_at=last.created_at if last else None,
    )


async def statuses(
    session: AsyncSession, now: datetime, *, driver_id: int | None = None
) -> list[PhotoStatus]:
    """Статус фотоконтроля работающих водителей с машиной. Только чтение: водителю
    без графика показываем срок «завтра», а заводит график ежедневная задача."""
    today = local_today(now)
    pairs = await _active_drivers_with_car(session, driver_id)
    ids = [d.id for d, _ in pairs]
    schedules = await _schedules_of(session, ids)
    last = await _last_inspections(session, ids)
    tomorrow = today + timedelta(days=1)
    return [
        _status(
            driver,
            car,
            schedules[driver.id].next_due_on if driver.id in schedules else tomorrow,
            last.get(driver.id),
            today,
        )
        for driver, car in pairs
    ]


async def _active_driver(session: AsyncSession, tg_user_id: int | None) -> Driver:
    """Работающий водитель с машиной по tg id; иначе 403 (не водитель) или 409 (без машины)."""
    driver = (
        await session.scalar(
            select(Driver).where(Driver.tg_user_id == tg_user_id, Driver.active.is_(True))
        )
        if tg_user_id is not None
        else None
    )
    if driver is None:
        raise DomainError("фотоконтроль сдаёт только работающий водитель", status_code=403)
    if driver.car_id is None:
        raise Conflict("за водителем не закреплена машина")
    return driver


async def _lock_driver(session: AsyncSession, driver_id: int) -> None:
    """Сериализует сдачи одного водителя до конца транзакции: два параллельных ретрая
    иначе оба не нашли бы повтор и завели два осмотра. На SQLite (тесты) гонки нет."""
    if session.bind.dialect.name == "postgresql":
        await session.execute(select(func.pg_advisory_xact_lock(_SUBMIT_LOCK_NS, driver_id)))


async def _recent_repeat(
    session: AsyncSession, driver_id: int, photos: dict[str, str], now: datetime
) -> PhotoInspection | None:
    """Осмотр с той же четвёркой file_id не старше REPEAT_WINDOW (граница включительно)."""
    return await session.scalar(
        select(PhotoInspection).where(
            PhotoInspection.driver_id == driver_id,
            PhotoInspection.created_at >= now - REPEAT_WINDOW,
            *(getattr(PhotoInspection, field) == value for field, value in photos.items()),
        )
    )


async def submit(
    session: AsyncSession,
    *,
    tg_user_id: int | None,
    front_file_id: str,
    rear_file_id: str,
    interior_front_file_id: str,
    interior_rear_file_id: str,
    has_issues: bool,
    comment: str | None,
    now: datetime,
) -> tuple[PhotoInspection, PhotoSchedule]:
    """Принимает осмотр от водителя (личность — по tg id, машина — из его записи).

    Повтор той же четвёрки фото в пределах REPEAT_WINDOW (бот переспросил после
    таймаута) возвращает уже принятый осмотр, а не заводит второй. В отличие от
    run_daily, коммитит сам: это одна операция API.

    Raises: DomainError(403) — не работающий водитель; Conflict (409) — нет машины;
    Validation (422) — одно фото на двух ракурсах или замечания без описания.
    """
    driver = await _active_driver(session, tg_user_id)
    photos = {
        "front_file_id": front_file_id,
        "rear_file_id": rear_file_id,
        "interior_front_file_id": interior_front_file_id,
        "interior_rear_file_id": interior_rear_file_id,
    }
    if len(set(photos.values())) < len(photos):
        raise Validation("одно и то же фото на разных ракурсах — нужен каждый ракурс отдельно")
    note = (comment or "").strip() or None
    if has_issues and note is None:
        raise Validation("опишите замечания по состоянию машины")

    await _lock_driver(session, driver.id)
    due = local_today(now) + timedelta(days=settings.photo_inspection_interval_days)
    repeated = await _recent_repeat(session, driver.id, photos, now)
    if repeated is not None:
        schedule = await _ensure_schedule(session, driver.id, due=due)
        await session.commit()  # отпускает advisory-lock
        return repeated, schedule

    inspection = PhotoInspection(
        driver_id=driver.id, car_id=driver.car_id, has_issues=has_issues, comment=note,
        created_at=now, **photos,
    )
    session.add(inspection)
    schedule = await _ensure_schedule(session, driver.id, due=due)
    schedule.next_due_on = due
    await alerts_domain.resolve_open(
        session, car_id=driver.car_id, atype=AlertType.photo_inspection_overdue, now=now
    )
    await session.commit()
    await session.refresh(inspection)
    return inspection, schedule


async def car_overdue_days(session: AsyncSession, car_id: int, now: datetime) -> int | None:
    """На сколько дней просрочен фотоконтроль водителя машины. None — водителя нет."""
    driver = await session.scalar(
        select(Driver).where(Driver.car_id == car_id, Driver.active.is_(True))
    )
    if driver is None:
        return None
    found = await statuses(session, now, driver_id=driver.id)
    return found[0].overdue_days if found else None


def reminder_text(status: PhotoStatus, today: date) -> str | None:
    """Текст водителю: накануне, в день срока и при просрочке; иначе None."""
    plate = escape(status.car_plate, quote=False)
    days_left = (status.next_due_on - today).days
    if days_left > 1:
        return None
    if days_left == 1:
        return f"📸 Завтра срок фотоконтроля машины {plate}.\n{_HOW}"
    if days_left == 0:
        return f"📸 Сегодня срок фотоконтроля машины {plate}.\n{_HOW}"
    return (
        f"⚠️ Фотоконтроль машины {plate} просрочен на {status.overdue_days} дн.\n"
        f"Пройдите его сегодня. {_HOW}"
    )


async def _remind(
    session: AsyncSession, status: PhotoStatus, schedule: PhotoSchedule, today: date
) -> bool:
    """Ставит водителю напоминание, если оно положено сегодня и ещё не уходило."""
    text = reminder_text(status, today)
    if text is None or schedule.last_reminded_on == today:
        return False
    await outbox.enqueue(
        session, recipient_tg_user_id=status.tg_user_id, text=text, kind=REMINDER_KIND
    )
    schedule.last_reminded_on = today
    return True


async def _alert_overdue(session: AsyncSession, status: PhotoStatus, now: datetime) -> None:
    """Поднимает (или обновляет открытый) алерт админу о просрочке фотоконтроля."""
    await alerts_domain.raise_alert(
        session,
        car_id=status.car_id,
        atype=AlertType.photo_inspection_overdue,
        payload={
            "driver_id": status.driver_id,
            "driver_name": status.driver_name,
            "plate": status.car_plate,
            "next_due_on": status.next_due_on.isoformat(),
            "overdue_days": status.overdue_days,
        },
        text=(
            f"фотоконтроль просрочен на {status.overdue_days} дн. "
            f"(водитель {status.driver_name}, срок был {status.next_due_on:%d.%m.%Y})"
        ),
        now=now,
    )


async def run_daily(session: AsyncSession, now: datetime) -> Counter[str]:
    """Ежедневный проход: заводит недостающие графики, шлёт напоминания (раз в день)
    и держит алерт админу по каждой машине с просрочкой. Коммит — за вызывающим.

    Каждый водитель — в своём SAVEPOINT: сбой на одном не отменяет остальных.
    """
    today = local_today(now)
    out: Counter[str] = Counter()
    pairs = await _active_drivers_with_car(session)
    ids = [d.id for d, _ in pairs]
    schedules = await _schedules_of(session, ids)
    last = await _last_inspections(session, ids)

    # Машины, чей алерт трогать нельзя: просрочены или упали в этом проходе (сбой
    # не должен молча закрыть настоящий алерт админу).
    keep_alert: set[int] = set()
    for driver, car in pairs:
        try:
            async with session.begin_nested():
                schedule = schedules.get(driver.id)
                if schedule is None:
                    schedule = await _ensure_schedule(
                        session, driver.id, due=today + timedelta(days=1)
                    )
                    out["started"] += 1
                status = _status(driver, car, schedule.next_due_on, last.get(driver.id), today)
                if await _remind(session, status, schedule, today):
                    out["reminders"] += 1
                if status.overdue_days > 0:
                    await _alert_overdue(session, status, now)
                    keep_alert.add(car.id)
                    out["overdue"] += 1
        except Exception:  # noqa: BLE001 — один водитель не роняет напоминания остальным
            log.exception("фотоконтроль: сбой по водителю %s", driver.id)
            keep_alert.add(car.id)
            out["errors"] += 1

    closed = await _close_stale_alerts(session, keep=keep_alert, now=now)
    if closed:
        out["resolved"] += closed
    await session.flush()
    return out


async def _close_stale_alerts(session: AsyncSession, *, keep: set[int], now: datetime) -> int:
    """Закрывает алерты о просрочке, которые не сняла сдача: водителя уволили, машину
    сменили, срок подвинули. `keep` — машины, чьи алерты не трогаем. Возвращает число
    закрытых (открытый алерт этого типа на машину один — его держит uq_alert_open_sys)."""
    query = select(Alert).where(
        Alert.type == AlertType.photo_inspection_overdue, Alert.status == AlertStatus.open
    )
    if keep:
        query = query.where(Alert.car_id.notin_(keep))
    stale = list(await session.scalars(query))
    for alert in stale:
        alert.status = AlertStatus.resolved
        alert.resolved_at = now
    await session.flush()
    return len(stale)


def _views_query():
    """Осмотр + имя водителя + номер машины (outer join: машину могли удалить)."""
    return (
        select(PhotoInspection, Driver.full_name, Car.plate)
        .join(Driver, Driver.id == PhotoInspection.driver_id)
        .outerjoin(Car, Car.id == PhotoInspection.car_id)
    )


async def get_inspection(session: AsyncSession, inspection_id: int) -> InspectionView | None:
    """Осмотр с именем водителя и номером машины — одним запросом."""
    row = (await session.execute(_views_query().where(PhotoInspection.id == inspection_id))).first()
    return InspectionView(*row) if row else None


async def list_inspections(
    session: AsyncSession, driver_id: int, *, limit: int = 10
) -> list[InspectionView]:
    """Последние осмотры водителя, свежие первыми."""
    rows = await session.execute(
        _views_query()
        .where(PhotoInspection.driver_id == driver_id)
        .order_by(PhotoInspection.created_at.desc(), PhotoInspection.id.desc())
        .limit(limit)
    )
    return [InspectionView(*row) for row in rows.all()]
