"""Штрафы: список, добавление, оплата, удаление, подсчёт неоплаченных для правил."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import NamedTuple, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Car, Driver, Fine, FineStatus
from app.errors import Conflict


@dataclass(frozen=True)
class FineImportRow:
    """Штраф от внешнего источника в терминах домена, без DTO транспорта."""

    plate: str
    external_ref: str
    amount: Decimal | None = None
    currency: str | None = None
    issued_at: datetime | None = None
    note: str | None = None


class FineImportOutcome(NamedTuple):
    """Итог пакетного импорта; поля именованные, чтобы их нельзя было перепутать."""

    created: int
    skipped: int
    unknown_plates: list[str]
    ambiguous_plates: list[str]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _window_start(window_days: int | None) -> datetime | None:
    # Окно — от now назад по issued_at (plan/06: fines_count.window_days).
    if window_days is None:
        return None
    return _now() - timedelta(days=window_days)


async def list_fines(
    session: AsyncSession,
    car_id: int,
    *,
    only_unpaid: bool = False,
    window_days: int | None = None,
) -> list[Fine]:
    stmt = select(Fine).where(Fine.car_id == car_id)
    if only_unpaid:
        stmt = stmt.where(Fine.status == FineStatus.unpaid)
    start = _window_start(window_days)
    if start is not None:
        stmt = stmt.where(Fine.issued_at >= start)
    result = await session.scalars(stmt.order_by(Fine.issued_at.desc()))
    return list(result.all())


async def get_fine(session: AsyncSession, fine_id: int) -> Fine | None:
    return await session.get(Fine, fine_id)


async def _same_fine_exists(
    session: AsyncSession, car_id: int, external_ref: str | None
) -> bool:
    """Отличает дубль от любой другой ошибки целостности."""
    if external_ref is None:
        return False
    found = await session.scalar(
        select(Fine.id).where(Fine.car_id == car_id, Fine.external_ref == external_ref)
    )
    return found is not None


async def add_fine(
    session: AsyncSession,
    car_id: int,
    *,
    driver_id: int | None = None,
    amount: Decimal | None = None,
    currency: str | None = None,
    issued_at: datetime | None = None,
    external_ref: str | None = None,
    note: str | None = None,
    created_by: int | None = None,
) -> Fine:
    if driver_id is None:
        # Водитель не указан — считаем, что за рулём был текущий закреплённый.
        driver_id = await session.scalar(
            select(Driver.id).where(Driver.car_id == car_id, Driver.active.is_(True))
        )
    fine = Fine(
        car_id=car_id,
        driver_id=driver_id,
        amount=amount,
        currency=currency,
        issued_at=issued_at or _now(),
        external_ref=external_ref,
        note=note,
        created_by=created_by,
    )
    session.add(fine)
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        if not await _same_fine_exists(session, car_id, external_ref):
            # Битый driver_id и прочие нарушения целостности не имеют отношения
            # к дублю: подменять их на 409 значит врать о причине.
            raise
        # Номер постановления бот вытаскивает из примечания эвристикой, так что
        # совпадения бывают и вручную: это ответ пользователю, а не 500.
        raise Conflict(
            f"штраф с номером {external_ref} по этой машине уже заведён"
        ) from None
    await session.refresh(fine)
    return fine


def normalize_plate(plate: str) -> str:
    """Во внешних источниках номер пишут с пробелами и дефисами, у нас — слитно."""
    return "".join(ch for ch in plate.upper() if ch.isalnum())


async def _build_plate_index(
    session: AsyncSession,
) -> tuple[dict[str, int], set[str]]:
    """Индекс «нормализованный номер → машина» плюс номера-двойники.

    «01KG139API» и «01 KG 139 API» — две записи парка на одну машину. Молча
    выбрать одну значит записать штраф не туда, поэтому они собираются отдельно.
    """
    rows = (await session.execute(select(Car.id, Car.plate))).all()
    by_plate: dict[str, int] = {}
    ambiguous: set[str] = set()
    for car_id, plate in rows:
        key = normalize_plate(plate)
        if key in by_plate:
            ambiguous.add(key)
        by_plate[key] = car_id
    return by_plate, ambiguous


async def _active_driver_by_car(session: AsyncSession) -> dict[int, int]:
    rows = await session.execute(
        select(Driver.car_id, Driver.id).where(Driver.active.is_(True))
    )
    return dict(rows.all())


async def _insert_or_skip(
    session: AsyncSession,
    fine: Fine,
) -> bool:
    """True — штраф заведён, False — такой уже был.

    Дубль отсекает уникальный индекс, а не проверка перед вставкой: два
    параллельных прогона иначе завели бы штраф дважды.
    """
    try:
        async with session.begin_nested():
            session.add(fine)
            await session.flush()
        return True
    except IntegrityError:
        if await _same_fine_exists(session, fine.car_id, fine.external_ref):
            return False
        # Не дубль, а что-то другое: молча зачислить в «пропущено» значит
        # потерять штраф.
        raise


async def import_fines(
    session: AsyncSession,
    items: Sequence[FineImportRow],
    *,
    source: str = "carcheck",
    created_by: int | None = None,
) -> FineImportOutcome:
    """Заводит найденные снаружи штрафы, пропуская уже известные."""
    by_plate, ambiguous_keys = await _build_plate_index(session)
    drivers = await _active_driver_by_car(session)

    created = 0
    skipped = 0
    unknown: dict[str, str] = {}
    ambiguous: dict[str, str] = {}
    for item in items:
        key = normalize_plate(item.plate)
        if key in ambiguous_keys:
            ambiguous.setdefault(key, item.plate)
            continue
        car_id = by_plate.get(key)
        if car_id is None:
            unknown.setdefault(key, item.plate)
            continue
        inserted = await _insert_or_skip(
            session,
            Fine(
                car_id=car_id,
                driver_id=drivers.get(car_id),
                amount=item.amount,
                currency=item.currency,
                issued_at=item.issued_at or _now(),
                source=source,
                external_ref=item.external_ref,
                note=item.note,
                created_by=created_by,
            ),
        )
        if inserted:
            created += 1
        else:
            skipped += 1
    await session.commit()
    return FineImportOutcome(
        created=created,
        skipped=skipped,
        unknown_plates=list(unknown.values()),
        ambiguous_plates=list(ambiguous.values()),
    )


async def pay_fine(session: AsyncSession, fine_id: int) -> Fine | None:
    fine = await get_fine(session, fine_id)
    if fine is None:
        return None
    fine.status = FineStatus.paid
    fine.paid_at = _now()
    await session.commit()
    await session.refresh(fine)
    return fine


async def delete_fine(session: AsyncSession, fine_id: int) -> bool:
    fine = await get_fine(session, fine_id)
    if fine is None:
        return False
    await session.delete(fine)
    await session.commit()
    return True


async def count_unpaid(
    session: AsyncSession, car_id: int, *, window_days: int | None = None
) -> int:
    stmt = select(func.count(Fine.id)).where(
        Fine.car_id == car_id, Fine.status == FineStatus.unpaid
    )
    start = _window_start(window_days)
    if start is not None:
        stmt = stmt.where(Fine.issued_at >= start)
    return int(await session.scalar(stmt) or 0)
