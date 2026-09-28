"""Задача проверки штрафов на tolom: суммы в алерте и номер вне реестра."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from app.db.models import AlertType, TaskRunStatus
from app.domain import alerts as alerts_domain
from app.domain import cars as cars_service
from app.domain import fines as fines_service
from app.fines_sources import CheckResult, ParsedViolation
from app.tasks.fines import (
    PlateScan,
    import_and_alert,
    money_summary,
    scan_plates,
    summarize,
)
from app.tasks.fines_tolom import HINT, SOURCE
from app.tolom.parser import parse_violations

UTC = timezone.utc


class FakeChecker:
    def __init__(self, answers: dict[str, CheckResult]) -> None:
        self.answers = answers

    def check(self, plate: str) -> CheckResult:
        return self.answers[plate]


def _noop() -> None:
    return None


def _payload(*refs: str, known: bool = True) -> dict:
    return {
        "currentInfo": {"success": known, "data": {"brand": "HYUNDAI"} if known else None},
        "penalties": {
            "success": known,
            "data": {
                "bgProtocols": [
                    {
                        "protocolNumber": ref,
                        "fineAmount": 1000.0,
                        "fineAmountToPay": 300.0,
                        "discountDaysLeft": 30,
                        "article": "Ст. 187",
                        "part": "ч. 1",
                        "violationTitle": "превышение скорости",
                        "violationDate": "2026-08-30T13:08:59",
                        "violationPlace": "а/д Балыкчы-Каракол",
                    }
                    for ref in refs
                ],
                "erpnProtocols": [],
            }
            if known
            else None,
        },
        "totalPenalties": len(refs),
    }


async def _car(session, plate="01KG100AAA"):
    return await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )


def _violation(ref, amount="1000", to_pay="300", days=30):
    return ParsedViolation(
        external_ref=ref,
        issued_at=datetime(2026, 8, 30, tzinfo=UTC),
        amount=Decimal(amount) if amount else None,
        note="Ст. 187 ч. 1 — превышение скорости",
        amount_to_pay=Decimal(to_pay) if to_pay else None,
        discount_days_left=days,
    )


# --- обход парка ------------------------------------------------------------


def test_scan_uses_tolom_parser():
    checker = FakeChecker({"A": CheckResult("A", payload=_payload("R1", "R2"))})

    scan = scan_plates(["A"], checker, pause=_noop, parse=parse_violations)

    assert [len(s.violations) for s in scan.scans] == [2]
    assert scan.scans[0].violations[0].amount_to_pay == Decimal("300")


def test_unregistered_plate_is_collected_not_silently_clean():
    """Опечатка в госномере обязана быть видна в журнале прогона."""
    checker = FakeChecker(
        {
            "A": CheckResult("A", payload=_payload("R1")),
            "B": CheckResult(
                "B", payload=_payload(known=False), plate_known=False
            ),
        }
    )

    scan = scan_plates(["A", "B"], checker, pause=_noop, parse=parse_violations)

    assert scan.unregistered == ["B"]
    status, detail = summarize(scan, 1)
    assert status is TaskRunStatus.ok
    assert "нет в реестре: B" in detail


# --- суммы в тексте алерта --------------------------------------------------


class _Fine:
    def __init__(self, amount, amount_to_pay=None):
        self.amount = amount
        self.amount_to_pay = amount_to_pay


def test_money_summary_shows_full_and_discounted():
    text = money_summary([_Fine(Decimal("1000"), Decimal("300")), _Fine(Decimal("1000"), Decimal("300"))])

    assert text == " на 2000 сом (со скидкой 600 сом)"


def test_money_summary_marks_partially_known_sums():
    """Часть штрафов без суммы: «на 1000» было бы неправдой."""
    text = money_summary([_Fine(Decimal("1000"), Decimal("300")), _Fine(None)])

    assert "не менее чем на 1000 сом" in text


def test_money_summary_is_empty_when_nothing_known():
    assert money_summary([_Fine(None), _Fine(None)]) == ""


def test_money_summary_without_discount_shows_one_number():
    assert money_summary([_Fine(Decimal("1000"))]) == " на 1000 сом"


# --- импорт -----------------------------------------------------------------


async def test_import_saves_both_amounts(session):
    car = await _car(session)

    await import_and_alert(
        session, [PlateScan(car.plate, [_violation("R1")])], source=SOURCE, hint=HINT
    )

    fines = await fines_service.list_fines(session, car.id)
    assert len(fines) == 1
    assert fines[0].amount == Decimal("1000")
    assert fines[0].amount_to_pay == Decimal("300")
    assert fines[0].discount_days_left == 30
    assert fines[0].source == SOURCE
    assert "превышение скорости" in fines[0].note


async def test_alert_text_carries_sums(session):
    car = await _car(session)

    await import_and_alert(
        session,
        [PlateScan(car.plate, [_violation("R1"), _violation("R2")])],
        source=SOURCE,
        hint=HINT,
    )

    alerts = await alerts_domain.list_alerts(session, status="open")
    assert alerts[0].type is AlertType.new_fine
    text = alerts[0].payload["text"]
    assert "новых штрафов: 2" in text
    assert "на 2000 сом (со скидкой 600 сом)" in text
    assert "tolom.kg" in text


async def test_fines_from_both_sources_do_not_double(session):
    """Один и тот же штраф из carcheck и из tolom — один номер постановления."""
    car = await _car(session)
    await import_and_alert(
        session,
        [PlateScan(car.plate, [ParsedViolation("R1", datetime(2026, 8, 30, tzinfo=UTC), None, "AFP")])],
        source="carcheck",
        hint="carcheck",
    )

    created = await import_and_alert(
        session, [PlateScan(car.plate, [_violation("R1")])], source=SOURCE, hint=HINT
    )

    assert created == 0
    assert len(await fines_service.list_fines(session, car.id)) == 1
