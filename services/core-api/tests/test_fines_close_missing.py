"""Закрытие пропавших штрафов — самое опасное место фичи.

Оплаченный штраф исчезает из ответа сервиса, и другого признака оплаты он не
даёт. Обратная сторона: любая причина, по которой ответ оказался неполным,
внешне выглядит точно так же — и без предохранителей один прогон «оплатил» бы
весь парк.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.db.models import FineStatus
from app.domain import cars as cars_service
from app.domain import fines as fines_service
from app.fines_sources import CheckResult, ParsedViolation
from app.tasks.fines import PlateScan, ScanResult, closable_plates, scan_plates, sync_and_alert
from app.tolom.parser import expected_counts, parse_violations

UTC = timezone.utc
SOURCE = "tolom"


async def _car(session, plate="01KG100AAA"):
    return await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )


def _violation(ref):
    return ParsedViolation(
        external_ref=ref,
        issued_at=datetime(2026, 8, 30, tzinfo=UTC),
        amount=None,
        note=None,
    )


async def _seed(session, car, *refs, source=SOURCE):
    """Заводит штрафы так же, как их завёл бы прогон источника."""
    await fines_service.import_fines(
        session,
        [
            fines_service.FineImportRow(plate=car.plate, external_ref=ref)
            for ref in refs
        ],
        source=source,
    )


_AS_MANY_AS_PARSED = object()


def _scan(plate, refs, *, expected=_AS_MANY_AS_PARSED, unparsed=0):
    """По умолчанию итог сервиса сходится с разобранным — обычный успешный ответ.

    `expected=None` означает «источник итогов не отдаёт вовсе» и от нуля
    отличается принципиально: ноль — это утверждение, None — незнание.
    """
    return ScanResult(
        scans=[
            PlateScan(
                plate,
                [_violation(r) for r in refs],
                unparsed,
                len(refs) if expected is _AS_MANY_AS_PARSED else expected,
            )
        ]
    )


async def _statuses(session, car):
    fines = await fines_service.list_fines(session, car.id)
    return {f.external_ref: f.status for f in fines}


# --- закрываем то, что действительно пропало --------------------------------


async def test_missing_fine_is_marked_paid(session):
    car = await _car(session)
    await _seed(session, car, "R1", "R2")

    report = await sync_and_alert(
        session, _scan(car.plate, ["R1"]), source=SOURCE, close=True
    )

    assert report.closed == 1
    statuses = await _statuses(session, car)
    assert statuses["R2"] is FineStatus.paid
    assert statuses["R1"] is FineStatus.unpaid
    paid = next(
        f for f in await fines_service.list_fines(session, car.id)
        if f.external_ref == "R2"
    )
    assert paid.paid_by == SOURCE, "видно, что отметил не человек"
    assert paid.paid_at is not None


async def test_car_with_all_fines_paid_is_closed_too(session):
    """Регрессия: ранний выход при пустом списке убивал ровно этот случай."""
    car = await _car(session)
    await _seed(session, car, "R1")

    report = await sync_and_alert(
        session, _scan(car.plate, []), source=SOURCE, close=True
    )

    assert report.closed == 1
    assert (await _statuses(session, car))["R1"] is FineStatus.paid


# --- чего не трогаем никогда ------------------------------------------------


async def test_manual_fine_is_never_closed(session):
    """Штраф, заведённый руками, в ответе сервиса не фигурирует по определению."""
    car = await _car(session)
    await fines_service.add_fine(
        session, car_id=car.id, external_ref="HAND1", created_by=1
    )

    report = await sync_and_alert(
        session, _scan(car.plate, []), source=SOURCE, close=True
    )

    assert report.closed == 0
    assert (await _statuses(session, car))["HAND1"] is FineStatus.unpaid


async def test_fine_without_ref_is_never_closed(session):
    car = await _car(session)
    await fines_service.add_fine(session, car_id=car.id, created_by=1)

    report = await sync_and_alert(
        session, _scan(car.plate, []), source=SOURCE, close=True
    )

    assert report.closed == 0


async def test_fine_of_another_source_is_left_alone(session):
    """carcheck-штраф, которого tolom никогда не видел, закрывать нечем."""
    car = await _car(session)
    await _seed(session, car, "C1", source="carcheck")

    report = await sync_and_alert(
        session, _scan(car.plate, []), source=SOURCE, close=True
    )

    assert report.closed == 0
    assert (await _statuses(session, car))["C1"] is FineStatus.unpaid


async def test_fine_seen_by_this_source_closes_even_if_created_by_other(session):
    """А тот же штраф, однажды подтверждённый tolom, закрыть можно."""
    car = await _car(session)
    await _seed(session, car, "C1", source="carcheck")
    await sync_and_alert(
        session, _scan(car.plate, ["C1"]), source=SOURCE, close=True
    )

    report = await sync_and_alert(
        session, _scan(car.plate, []), source=SOURCE, close=True
    )

    assert report.closed == 1
    assert (await _statuses(session, car))["C1"] is FineStatus.paid


async def test_paid_fine_is_not_touched_twice(session):
    car = await _car(session)
    await _seed(session, car, "R1")
    await sync_and_alert(session, _scan(car.plate, []), source=SOURCE, close=True)
    first = next(iter(await fines_service.list_fines(session, car.id)))
    stamp = first.paid_at

    report = await sync_and_alert(
        session, _scan(car.plate, []), source=SOURCE, close=True
    )

    assert report.closed == 0
    again = next(iter(await fines_service.list_fines(session, car.id)))
    assert again.paid_at == stamp


async def test_car_absent_from_the_answer_is_not_closed(session):
    """Машину не проверяли — молчание про неё ничего не значит."""
    car = await _car(session)
    other = await _car(session, "01KG200BBB")
    await _seed(session, car, "R1")

    report = await sync_and_alert(
        session, _scan(other.plate, []), source=SOURCE, close=True
    )

    assert report.closed == 0
    assert (await _statuses(session, car))["R1"] is FineStatus.unpaid


# --- фильтры доверия к ответу -----------------------------------------------


async def test_unregistered_plate_closes_nothing(session):
    """Опечатка в госномере не должна «оплачивать» машину."""
    car = await _car(session)
    await _seed(session, car, "R1")
    scan = _scan(car.plate, [])
    scan.unregistered.append(car.plate)

    report = await sync_and_alert(session, scan, source=SOURCE, close=True)

    assert report.closed == 0
    assert (await _statuses(session, car))["R1"] is FineStatus.unpaid


async def test_unparsed_records_close_nothing(session):
    """Часть записей не разобрана — набор пришедших номеров неполон."""
    car = await _car(session)
    await _seed(session, car, "R1")

    report = await sync_and_alert(
        session, _scan(car.plate, [], unparsed=1), source=SOURCE, close=True
    )

    assert report.closed == 0


async def test_totals_mismatch_closes_nothing(session):
    """Сервис насчитал больше, чем мы разобрали, — значит видим не всё."""
    car = await _car(session)
    await _seed(session, car, "R1", "R2")

    report = await sync_and_alert(
        session, _scan(car.plate, ["R1"], expected=2), source=SOURCE, close=True
    )

    assert report.closed == 0
    assert (await _statuses(session, car))["R2"] is FineStatus.unpaid


async def test_source_without_totals_never_closes(session):
    """У carcheck итогов нет вовсе, и пустой ответ ничего не доказывает."""
    car = await _car(session)
    await _seed(session, car, "R1", source="carcheck")

    report = await sync_and_alert(
        session,
        _scan(car.plate, [], expected=None),
        source="carcheck",
        close=True,
    )

    assert report.closed == 0


async def test_refused_run_closes_nothing(session):
    """Отказ прерывает обход: непроверенные номера не должны выглядеть чистыми."""
    car = await _car(session)
    await _seed(session, car, "R1")
    scan = ScanResult(refused=[{"plate": car.plate, "reason": "капча"}])

    report = await sync_and_alert(session, scan, source=SOURCE, close=True)

    assert report.closed == 0


async def test_closing_is_off_by_default(session):
    """Без явного close=True прогон не закрывает ничего."""
    car = await _car(session)
    await _seed(session, car, "R1")

    report = await sync_and_alert(session, _scan(car.plate, []), source=SOURCE)

    assert report.closed == 0


# --- предохранитель ---------------------------------------------------------


async def test_mass_disappearance_is_refused_entirely(session):
    """Пропало больше, чем бывает оплат: скорее сломался ответ, чем удачный день."""
    car = await _car(session)
    await _seed(session, car, "R1", "R2", "R3")

    report = await sync_and_alert(
        session, _scan(car.plate, []), source=SOURCE, close=True, close_limit=2
    )

    assert report.closed == 0
    assert report.close_skipped is not None
    assert all(s is FineStatus.unpaid for s in (await _statuses(session, car)).values())


async def test_refused_closing_is_visible_in_the_run_log(session):
    """Отменённое закрытие снаружи выглядит как «оплаченных не нашлось»."""
    from app.tasks.fines import summarize

    car = await _car(session)
    await _seed(session, car, "R1", "R2")
    scan = _scan(car.plate, [])
    report = await sync_and_alert(
        session, scan, source=SOURCE, close=True, close_limit=1
    )

    _, detail = summarize(scan, report)

    assert "закрытие пропущено" in detail


# --- отбор номеров, пригодных к закрытию ------------------------------------


def test_closable_plates_requires_matching_totals():
    scan = ScanResult(
        scans=[
            PlateScan("A", [_violation("R1")], 0, 1),
            PlateScan("B", [_violation("R2")], 0, 5),
            PlateScan("C", [], 0, None),
        ]
    )

    assert closable_plates(scan) == {"A": {"R1"}}


def test_renamed_container_does_not_look_like_a_paid_fleet():
    """Сервис молча переименовал ключ: разбор пуст, номер известен, итог — нет."""
    payload = {
        "currentInfo": {"success": True, "data": {"brand": "HYUNDAI"}},
        "penalties": {"success": True, "data": {"newNameProtocols": [{"protocolNumber": "R1"}]}},
        "totalPenalties": 1,
    }
    checker = type("C", (), {"check": lambda self, p: CheckResult(p, payload=payload)})()

    scan = scan_plates(
        ["A"], checker, pause=lambda: None, parse=parse_violations, expected=expected_counts
    )

    assert scan.scans[0].violations == []
    assert closable_plates(scan) == {}, "иначе весь парк выглядел бы оплаченным"
