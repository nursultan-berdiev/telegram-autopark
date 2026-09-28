"""Обновление уже заведённых штрафов при каждом успешном прогоне.

До этого повторный прогон только пропускал известный штраф, поэтому
`discount_days_left` замерзал на значении первого импорта и с каждым днём
врал сильнее, а сумма со скидкой не отражала истечение срока.
"""
from __future__ import annotations

from datetime import date, timezone
from decimal import Decimal

from app.db.models import FineStatus
from app.domain import cars as cars_service
from app.domain import fines as fines_service
from app.domain.fines import discount_deadline

UTC = timezone.utc


async def _car(session, plate="01KG100AAA"):
    return await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )


def _row(plate, ref="R1", **kwargs):
    return fines_service.FineImportRow(plate=plate, external_ref=ref, **kwargs)


async def _only(session, car):
    return (await fines_service.list_fines(session, car.id))[0]


# --- срок скидки ------------------------------------------------------------


def test_deadline_counts_from_delivery():
    assert discount_deadline(date(2026, 9, 1), 30) == date(2026, 10, 1)


def test_no_delivery_means_no_deadline():
    """Не вручено — отсчёт не начался, и выдумывать дату неоткуда."""
    assert discount_deadline(None, 30) is None


def test_no_days_means_no_deadline():
    assert discount_deadline(date(2026, 9, 1), None) is None


# --- обновление -------------------------------------------------------------


async def test_repeat_run_refreshes_discount(session):
    car = await _car(session)
    await fines_service.import_fines(
        session,
        [_row(car.plate, amount=Decimal("1000"), amount_to_pay=Decimal("300"), discount_days_left=30)],
        source="tolom",
    )

    outcome = await fines_service.import_fines(
        session,
        [_row(car.plate, amount=Decimal("1000"), amount_to_pay=Decimal("1000"), discount_days_left=0)],
        source="tolom",
    )

    fine = await _only(session, car)
    assert outcome.updated == 1
    assert fine.amount_to_pay == Decimal("1000"), "скидка истекла — платить полную"
    assert fine.discount_days_left == 0


async def test_repeat_run_without_changes_is_not_counted_as_update(session):
    car = await _car(session)
    row = _row(car.plate, amount=Decimal("1000"))
    await fines_service.import_fines(session, [row], source="tolom")

    outcome = await fines_service.import_fines(session, [row], source="tolom")

    assert outcome.updated == 0
    assert outcome.skipped == 1


async def test_carcheck_does_not_erase_tolom_data(session):
    """У carcheck сумм нет вовсе, и пустое там означает «не знаю»."""
    car = await _car(session)
    await fines_service.import_fines(
        session,
        [
            _row(
                car.plate,
                amount=Decimal("1000"),
                amount_to_pay=Decimal("300"),
                article="Ст. 187 ч. 1",
                place="а/д Балыкчы-Каракол",
            )
        ],
        source="tolom",
    )

    await fines_service.import_fines(session, [_row(car.plate)], source="carcheck")

    fine = await _only(session, car)
    assert fine.amount == Decimal("1000")
    assert fine.amount_to_pay == Decimal("300")
    assert fine.article == "Ст. 187 ч. 1"
    assert fine.place == "а/д Балыкчы-Каракол"


async def test_source_of_an_existing_fine_is_not_rewritten(session):
    car = await _car(session)
    await fines_service.import_fines(session, [_row(car.plate)], source="carcheck")

    await fines_service.import_fines(session, [_row(car.plate)], source="tolom")

    fine = await _only(session, car)
    assert fine.source == "carcheck", "кто завёл — то и источник"
    assert fine.last_seen_source == "tolom", "а кто видел последним — видно отдельно"


async def test_paid_fine_is_never_resurrected(session):
    """Админ мог отметить оплату раньше, чем сервис это увидел."""
    car = await _car(session)
    await fines_service.import_fines(session, [_row(car.plate)], source="tolom")
    fine = await _only(session, car)
    await fines_service.pay_fine(session, fine.id)

    await fines_service.import_fines(session, [_row(car.plate)], source="tolom")

    fine = await _only(session, car)
    assert fine.status is FineStatus.paid
    assert fine.paid_by == "admin"


async def test_note_of_a_known_fine_is_left_alone(session):
    """В примечании заметка человека — источнику там делать нечего."""
    car = await _car(session)
    await fines_service.add_fine(
        session, car_id=car.id, external_ref="R1", note="разбирался лично", created_by=1
    )

    await fines_service.import_fines(
        session, [_row(car.plate, note="AFP")], source="carcheck"
    )

    assert (await _only(session, car)).note == "разбирался лично"


async def test_last_seen_is_stamped(session):
    car = await _car(session)

    await fines_service.import_fines(session, [_row(car.plate)], source="tolom")

    fine = await _only(session, car)
    assert fine.last_seen_at is not None
    assert fine.last_seen_source == "tolom"


# --- дата скидки не уезжает назад -------------------------------------------


async def test_deadline_is_set_on_creation(session):
    car = await _car(session)

    await fines_service.import_fines(
        session,
        [_row(car.plate, delivery_date=date(2026, 9, 1), discount_days_left=30)],
        source="tolom",
    )

    assert (await _only(session, car)).discount_until == date(2026, 10, 1)


async def test_deadline_never_moves_backwards(session):
    """Если discountDaysLeft окажется обратным отсчётом, дата поехала бы каждый день."""
    car = await _car(session)
    await fines_service.import_fines(
        session,
        [_row(car.plate, delivery_date=date(2026, 9, 1), discount_days_left=30)],
        source="tolom",
    )

    await fines_service.import_fines(
        session,
        [_row(car.plate, delivery_date=date(2026, 9, 1), discount_days_left=20)],
        source="tolom",
    )

    assert (await _only(session, car)).discount_until == date(2026, 10, 1)


async def test_delivery_appearing_later_sets_the_deadline(session):
    """Постановление вручили между прогонами — срок скидки наконец пошёл."""
    car = await _car(session)
    await fines_service.import_fines(
        session, [_row(car.plate, discount_days_left=30)], source="tolom"
    )
    assert (await _only(session, car)).discount_until is None

    await fines_service.import_fines(
        session,
        [_row(car.plate, delivery_date=date(2026, 9, 10), discount_days_left=30)],
        source="tolom",
    )

    assert (await _only(session, car)).discount_until == date(2026, 10, 10)
