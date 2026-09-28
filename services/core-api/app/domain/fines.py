"""Штрафы: список, добавление, оплата, удаление, подсчёт неоплаченных для правил."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Mapping, NamedTuple, Sequence

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Car, Driver, Fine, FineStatus
from app.errors import Conflict

log = logging.getLogger(__name__)

# Поля, которые внешний источник переписывает на каждом прогоне. Намеренно
# не входят: issued_at (дата нарушения не меняется), note (там заметка
# человека), status и driver_id (это наши решения, а не сервиса).
SOURCE_FIELDS = (
    "amount",
    "amount_to_pay",
    "discount_days_left",
    "currency",
    "article",
    "violation_title",
    "place",
    "payment_code",
    "protocol_kind",
    "delivery_date",
)


@dataclass(frozen=True)
class FineImportRow:
    """Штраф от внешнего источника в терминах домена, без DTO транспорта."""

    plate: str
    external_ref: str
    amount: Decimal | None = None
    # Сумма со скидкой приходит не от всех источников: carcheck сумм не
    # отдаёт вовсе, и пустое здесь — нормальное состояние, а не потеря.
    amount_to_pay: Decimal | None = None
    discount_days_left: int | None = None
    currency: str | None = None
    issued_at: datetime | None = None
    article: str | None = None
    violation_title: str | None = None
    place: str | None = None
    payment_code: str | None = None
    protocol_kind: str | None = None
    delivery_date: date | None = None
    note: str | None = None


class FineImportOutcome(NamedTuple):
    """Итог пакетного импорта; поля именованные, чтобы их нельзя было перепутать.

    `created_per_plate` и `car_id_per_plate` избавляют вызывающего от второго
    резолва машины по номеру: индекс уже построен здесь, причём с нормализацией,
    а поиск по точному совпадению строки имел бы другую семантику.
    """

    created: int
    skipped: int
    unknown_plates: list[str]
    ambiguous_plates: list[str]
    # Без дефолтов: у typing.NamedTuple дефолт — один объект на все
    # экземпляры, и общий словарь испортился бы для всех сразу.
    created_ids_per_plate: dict[str, list[int]]
    car_id_per_plate: dict[str, int]
    updated: int
    closed: int
    # Заполняется, когда закрытие отменено предохранителем: молчать о таком
    # нельзя — снаружи это выглядело бы как «оплаченных не нашлось».
    close_skipped: str | None


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


async def get_fine_with_plate(
    session: AsyncSession, fine_id: int
) -> tuple[Fine, str] | None:
    """Штраф вместе с номером машины: карточке он нужен всегда."""
    result = await session.execute(
        select(Fine, Car.plate).join(Car, Car.id == Fine.car_id).where(Fine.id == fine_id)
    )
    row = result.first()
    return (row[0], row[1]) if row else None


async def list_fleet_fines(
    session: AsyncSession,
    *,
    car_id: int | None = None,
    only_unpaid: bool = True,
    limit: int = 200,
) -> list[tuple[Fine, str]]:
    """Штрафы по всему парку или по одной машине, вместе с номерами.

    Сортировка по машине, а затем по дате: группировку на экране остаётся
    только отрисовать, пересортировывать на клиенте нечего.
    """
    stmt = (
        select(Fine, Car.plate)
        .join(Car, Car.id == Fine.car_id)
        .order_by(Car.plate, Fine.issued_at.desc())
        .limit(limit)
    )
    if car_id is not None:
        stmt = stmt.where(Fine.car_id == car_id)
    if only_unpaid:
        stmt = stmt.where(Fine.status == FineStatus.unpaid)
    result = await session.execute(stmt)
    return [(row[0], row[1]) for row in result.all()]


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


def discount_deadline(delivery: date | None, days_left: int | None) -> date | None:
    """Последний день скидки.

    Срок идёт от ВРУЧЕНИЯ постановления: пока `delivery_date` пуст, отсчёт не
    начался, и предельной даты не существует — выдумывать её неоткуда.
    """
    if delivery is None or days_left is None or days_left < 0:
        return None
    return delivery + timedelta(days=days_left)


def _apply_source_fields(fine: Fine, row: FineImportRow) -> bool:
    """Переносит в штраф то, что сервис прислал сейчас. True — что-то изменилось.

    Обновляем ТОЛЬКО не-`None`: у carcheck сумм и подробностей нет вовсе, и
    пустое там означает «не знаю», а не «значения нет». Слепое присваивание
    затёрло бы данные tolom при первом же прогоне запасного источника.
    """
    changed = False
    for field in SOURCE_FIELDS:
        value = getattr(row, field)
        if value is not None and getattr(fine, field) != value:
            setattr(fine, field, value)
            changed = True

    deadline = discount_deadline(fine.delivery_date, fine.discount_days_left)
    if deadline is not None:
        if fine.discount_until is None or deadline > fine.discount_until:
            fine.discount_until = deadline
            changed = True
        elif deadline < fine.discount_until:
            # Дата скидки не может уезжать назад: если сервис считает
            # discountDaysLeft обратным отсчётом, сумма «вручение + остаток»
            # уменьшалась бы каждый день. Держим самый ранний расчёт.
            log.warning(
                "срок скидки по %s уехал бы назад: %s → %s",
                fine.external_ref,
                fine.discount_until,
                deadline,
            )
    return changed


async def _insert_or_update(
    session: AsyncSession,
    fine: Fine,
    row: FineImportRow,
    *,
    seen_at: datetime,
    source: str,
) -> str:
    """Заводит штраф или обновляет уже известный: "created" | "updated" | "same".

    Дубль отсекает уникальный индекс, а не проверка перед вставкой: два
    параллельных прогона иначе завели бы штраф дважды.
    """
    fine.last_seen_at = seen_at
    fine.last_seen_source = source
    try:
        async with session.begin_nested():
            session.add(fine)
            await session.flush()
        return "created"
    except IntegrityError:
        existing = await _get_by_ref(session, fine.car_id, fine.external_ref)
        if existing is None:
            # Не дубль, а что-то другое: молча зачислить в «пропущено» значит
            # потерять штраф.
            raise
        changed = _apply_source_fields(existing, row)
        existing.last_seen_at = seen_at
        existing.last_seen_source = source
        # `status` не трогаем никогда: админ мог отметить оплату раньше, чем
        # сервис это увидел, и воскрешать штраф нельзя.
        return "updated" if changed else "same"


async def _get_by_ref(
    session: AsyncSession, car_id: int, external_ref: str | None
) -> Fine | None:
    if external_ref is None:
        return None
    result = await session.execute(
        select(Fine).where(Fine.car_id == car_id, Fine.external_ref == external_ref)
    )
    return result.scalars().first()


async def close_missing(
    session: AsyncSession,
    *,
    car_id: int,
    seen_refs: set[str],
    source: str,
    now: datetime | None = None,
) -> list[Fine]:
    """Ищет штрафы, пропавшие из ответа сервиса. Пометку делает вызывающий.

    Оплаченный штраф исчезает из ответа — другого признака оплаты сервис не
    даёт. Закрываем только то, что этот источник когда-либо видел сам
    (`source` или `last_seen_source`): ручной штраф в ответе не фигурирует по
    определению, и «пропажей» он выглядит всегда.

    Вызывать можно ТОЛЬКО по машине с полностью разобранным успешным ответом —
    решение об этом принимает вызывающий (см. app/tasks/fines.py).
    """
    now = now or _now()
    result = await session.execute(
        select(Fine).where(
            Fine.car_id == car_id,
            Fine.status == FineStatus.unpaid,
            Fine.external_ref.is_not(None),
            (Fine.source == source) | (Fine.last_seen_source == source),
        )
    )
    return [f for f in result.scalars() if f.external_ref not in seen_refs]


def mark_paid(fines: Sequence[Fine], *, paid_by: str, now: datetime) -> None:
    """Отмечает оплату. `last_seen_at` не трогаем: это «когда видели», а не «когда закрыли»."""
    for fine in fines:
        fine.status = FineStatus.paid
        fine.paid_at = now
        fine.paid_by = paid_by


async def import_fines(
    session: AsyncSession,
    items: Sequence[FineImportRow],
    *,
    source: str = "carcheck",
    created_by: int | None = None,
    commit: bool = True,
    closable: Mapping[str, set[str]] | None = None,
    close_limit: int | None = None,
) -> FineImportOutcome:
    """Сводит найденное снаружи с базой: заводит новое, обновляет известное.

    `closable` — номера машин, по которым ответ сервиса был полным и успешным,
    с набором пришедших номеров постановлений. Только по ним можно считать,
    что пропавший штраф оплачен; решение принимает вызывающий, потому что
    здесь не видно ни отказов сервиса, ни неразобранных записей.

    `commit=False` отдаёт фиксацию вызывающему: импорт, закрытие и уведомление
    должны быть атомарны, иначе штраф ложится в базу, уведомление падает, а
    следующий прогон считает его уже не новым — и о нём никто не узнает.
    """
    by_plate, ambiguous_keys = await _build_plate_index(session)
    drivers = await _active_driver_by_car(session)
    now = _now()

    created = 0
    updated = 0
    skipped = 0
    unknown: dict[str, str] = {}
    ambiguous: dict[str, str] = {}
    created_ids_per_plate: dict[str, list[int]] = {}
    car_id_per_plate: dict[str, int] = {}
    for item in items:
        key = normalize_plate(item.plate)
        if key in ambiguous_keys:
            ambiguous.setdefault(key, item.plate)
            continue
        car_id = by_plate.get(key)
        if car_id is None:
            unknown.setdefault(key, item.plate)
            continue
        fine = Fine(
            car_id=car_id,
            driver_id=drivers.get(car_id),
            amount=item.amount,
            amount_to_pay=item.amount_to_pay,
            discount_days_left=item.discount_days_left,
            currency=item.currency,
            issued_at=item.issued_at or now,
            source=source,
            external_ref=item.external_ref,
            article=item.article,
            violation_title=item.violation_title,
            place=item.place,
            payment_code=item.payment_code,
            protocol_kind=item.protocol_kind,
            delivery_date=item.delivery_date,
            discount_until=discount_deadline(
                item.delivery_date, item.discount_days_left
            ),
            note=item.note,
            created_by=created_by,
        )
        outcome = await _insert_or_update(
            session, fine, item, seen_at=now, source=source
        )
        car_id_per_plate[item.plate] = car_id
        if outcome == "created":
            created += 1
            created_ids_per_plate.setdefault(item.plate, []).append(fine.id)
        else:
            skipped += 1
            updated += outcome == "updated"

    closed, close_skipped = await _close_all_missing(
        session,
        closable=closable or {},
        by_plate=by_plate,
        ambiguous_keys=ambiguous_keys,
        source=source,
        close_limit=close_limit,
        now=now,
    )

    if commit:
        await session.commit()
    return FineImportOutcome(
        created=created,
        skipped=skipped,
        unknown_plates=list(unknown.values()),
        ambiguous_plates=list(ambiguous.values()),
        created_ids_per_plate=created_ids_per_plate,
        car_id_per_plate=car_id_per_plate,
        updated=updated,
        closed=closed,
        close_skipped=close_skipped,
    )


async def _close_all_missing(
    session: AsyncSession,
    *,
    closable: Mapping[str, set[str]],
    by_plate: dict[str, int],
    ambiguous_keys: set[str],
    source: str,
    close_limit: int | None,
    now: datetime,
) -> tuple[int, str | None]:
    """Закрывает пропавшие по всем машинам разом — с предохранителем.

    Предохранитель нужен на случай, которого не покрывает ни один фильтр
    вызывающего: сервис молча переименовал контейнер, разбор вернул пусто по
    всему парку, и «оплаченным» выглядит сразу всё. Массовое закрытие лучше
    отменить целиком и сказать об этом, чем сделать наполовину.
    """
    candidates: list[Fine] = []
    for plate, seen_refs in closable.items():
        key = normalize_plate(plate)
        if key in ambiguous_keys:
            continue
        car_id = by_plate.get(key)
        if car_id is None:
            continue
        candidates.extend(
            await close_missing(
                session, car_id=car_id, seen_refs=seen_refs, source=source, now=now
            )
        )

    if close_limit is not None and len(candidates) > close_limit:
        reason = f"за прогон пропало {len(candidates)} штрафов, порог {close_limit}"
        log.error("закрытие отменено: %s", reason)
        return 0, reason
    mark_paid(candidates, paid_by=source, now=now)
    return len(candidates), None


async def pay_fine(
    session: AsyncSession, fine_id: int, *, paid_by: str = "admin"
) -> Fine | None:
    fine = await get_fine(session, fine_id)
    if fine is None:
        return None
    fine.status = FineStatus.paid
    fine.paid_at = _now()
    fine.paid_by = paid_by
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
