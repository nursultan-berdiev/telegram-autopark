"""Задача проверки штрафов: обход парка и уведомление о новом штрафе."""
from __future__ import annotations

from datetime import datetime, timezone

from app.carcheck.browser import CheckResult
from app.carcheck.parser import ParsedViolation
from app.db.models import AlertType, TaskRunStatus
from app.domain import alerts as alerts_domain
from app.domain import cars as cars_service
from app.domain import fines as fines_service
from app.tasks.fines import PlateScan, import_and_alert, scan_plates, summarize

UTC = timezone.utc


class FakeChecker:
    """Подставной браузер: сеть в тестах не нужна."""

    def __init__(self, answers: dict[str, CheckResult]) -> None:
        self.answers = answers
        self.asked: list[str] = []

    def check(self, plate: str) -> CheckResult:
        self.asked.append(plate)
        return self.answers[plate]


def _payload(*refs: str) -> dict:
    return {
        "vehicle": {"success": True, "data": {"govPlate": "X"}},
        "violations": {
            "success": True,
            "data": [
                {"violationType": "AFP", "protocolNumber": r, "violationDate": "2026-08-10T20:06:04"}
                for r in refs
            ],
        },
    }


def _noop() -> None:
    return None


# --- обход парка ------------------------------------------------------------


def test_scan_collects_violations_per_plate():
    checker = FakeChecker(
        {"A": CheckResult("A", payload=_payload("AM1", "AM2")),
         "B": CheckResult("B", payload=_payload())}
    )

    scan = scan_plates(["A", "B"], checker, pause=_noop)

    assert scan.checked == 2
    assert [len(s.violations) for s in scan.scans] == [2, 0]


def test_refusal_stops_the_whole_run():
    """Отказ — это ответ про нас, а не про номер: перебор дальше только вредит."""
    checker = FakeChecker(
        {"A": CheckResult("A", refused="CAPTCHA_LOW_SCORE"),
         "B": CheckResult("B", payload=_payload("AM1"))}
    )

    scan = scan_plates(["A", "B"], checker, pause=_noop)

    assert checker.asked == ["A"], "второй номер спрашивать не должны"
    assert scan.refused[0]["reason"] == "CAPTCHA_LOW_SCORE"


def test_single_plate_failure_does_not_stop_the_rest():
    checker = FakeChecker(
        {"A": CheckResult("A", error="сервис не ответил"),
         "B": CheckResult("B", payload=_payload("AM1"))}
    )

    scan = scan_plates(["A", "B"], checker, pause=_noop)

    assert scan.checked == 1
    assert scan.failed[0]["plate"] == "A"


def test_pause_between_plates_but_not_after_last():
    calls = []
    checker = FakeChecker({p: CheckResult(p, payload=_payload()) for p in "ABC"})

    scan_plates(["A", "B", "C"], checker, pause=lambda: calls.append(1))

    assert len(calls) == 2, "пауза нужна между номерами, а не после последнего"


# --- исход прогона ----------------------------------------------------------


def test_refusal_is_not_reported_as_success():
    scan = scan_plates(
        ["A"], FakeChecker({"A": CheckResult("A", refused="CAPTCHA_LOW_SCORE")}), pause=_noop
    )

    status, detail = summarize(scan, 0)

    assert status is TaskRunStatus.refused
    assert "отказал" in detail


def test_empty_result_is_success_not_failure():
    scan = scan_plates(["A"], FakeChecker({"A": CheckResult("A", payload=_payload())}), pause=_noop)

    status, _ = summarize(scan, 0)

    assert status is TaskRunStatus.ok, "«штрафов нет» — нормальный исход"


def test_all_plates_failed_is_failure():
    scan = scan_plates(["A"], FakeChecker({"A": CheckResult("A", error="таймаут")}), pause=_noop)

    status, _ = summarize(scan, 0)

    assert status is TaskRunStatus.failed


# --- импорт и алерт ---------------------------------------------------------


async def _car(session, plate="01KG100AAA"):
    return await cars_service.create_car(
        session, plate=plate, model=None, photo_file_id=None, photo_path=None
    )


def _violation(ref):
    return ParsedViolation(
        external_ref=ref, issued_at=datetime(2026, 8, 10, tzinfo=UTC), amount=None, note="AFP"
    )


async def test_new_fine_raises_alert_with_counts(session):
    car = await _car(session)

    created = await import_and_alert(session, [PlateScan(car.plate, [_violation("AM1"), _violation("AM2")])])

    assert created == 2
    alerts = await alerts_domain.list_alerts(session, status="open")
    assert len(alerts) == 1
    assert alerts[0].type is AlertType.new_fine
    assert alerts[0].payload["new"] == 2
    assert "Сумму смотрите" in alerts[0].payload["text"]


async def test_repeat_run_adds_nothing_and_stays_silent(session):
    """Те же штрафы, что вчера, — не повод будить админа."""
    from app.db.models import AlertStatus

    car = await _car(session)
    await import_and_alert(session, [PlateScan(car.plate, [_violation("AM1")])])
    opened = await alerts_domain.list_alerts(session, status="open")
    await alerts_domain.set_status(session, opened[0], AlertStatus.resolved)
    await session.commit()

    created = await import_and_alert(session, [PlateScan(car.plate, [_violation("AM1")])])

    assert created == 0
    assert await alerts_domain.list_alerts(session, status="open") == []


async def test_fine_without_amount_is_still_imported(session):
    """Сервис суммы не отдаёт — штраф всё равно должен попасть в базу."""
    car = await _car(session)

    await import_and_alert(session, [PlateScan(car.plate, [_violation("AM1")])])

    fines = await fines_service.list_fines(session, car.id)
    assert len(fines) == 1
    assert fines[0].amount is None
    assert fines[0].source == "carcheck"


# Атомарность импорта и алерта на SQLite не проверить: pysqlite освобождает
# SAVEPOINT так, что вложенная вставка переживает откат внешней транзакции.
# Проверено на PostgreSQL отдельно — см. описание PR.


# --- серия отказов ----------------------------------------------------------


async def _run_row(session, status, task=None):
    from app.db.models import TaskRun
    from app.tasks.fines import NAME

    row = TaskRun(task=task or NAME, status=status)
    session.add(row)
    await session.commit()
    return row


async def test_consecutive_failures_counts_until_first_success(session):
    """Отказ капчи снаружи неотличим от «штрафов нет» — считаем серию."""
    from app.domain import periodic as periodic_service
    from app.tasks.fines import NAME

    await _run_row(session, TaskRunStatus.ok)
    await _run_row(session, TaskRunStatus.refused)
    await _run_row(session, TaskRunStatus.failed)

    assert await periodic_service.consecutive_failures(session, NAME) == 2


async def test_success_resets_failure_streak(session):
    from app.domain import periodic as periodic_service
    from app.tasks.fines import NAME

    await _run_row(session, TaskRunStatus.refused)
    await _run_row(session, TaskRunStatus.ok)

    assert await periodic_service.consecutive_failures(session, NAME) == 0


async def test_runs_of_other_tasks_do_not_mix(session):
    from app.domain import periodic as periodic_service
    from app.tasks.fines import NAME

    await _run_row(session, TaskRunStatus.refused, task="other.task")

    assert await periodic_service.consecutive_failures(session, NAME) == 0


# --- уведомление не должно теряться -----------------------------------------


async def test_second_batch_creates_a_new_alert(session):
    """Иначе вторая партия штрафов не дойдёт до оператора никогда.

    Бот дедуплицирует доставку по id алерта. Если перезаписать payload
    открытого, он останется показанным один раз — с устаревшими цифрами.
    """
    car = await _car(session)
    await import_and_alert(session, [PlateScan(car.plate, [_violation("AM1")])])
    first = (await alerts_domain.list_alerts(session, status="open"))[0]

    await import_and_alert(session, [PlateScan(car.plate, [_violation("AM2")])])

    opened = await alerts_domain.list_alerts(session, status="open")
    assert len(opened) == 1
    assert opened[0].id != first.id, "нужен новый алерт, а не перезапись старого"
    assert opened[0].payload["new"] == 1
    assert opened[0].payload["unpaid_total"] == 2


async def test_fleet_imported_in_one_pass(session):
    """Импорт вызывается один раз на прогон, а не на каждый номер."""
    first = await _car(session, "01KG100AAA")
    second = await _car(session, "01KG300CCC")

    created = await import_and_alert(
        session,
        [PlateScan(first.plate, [_violation("AM1")]), PlateScan(second.plate, [_violation("AM2")])],
    )

    assert created == 2
    assert len(await alerts_domain.list_alerts(session, status="open")) == 2


async def test_missing_car_does_not_stop_the_batch(session, monkeypatch):
    """Машина исчезла между прогоном и импортом — остальные должны доехать."""
    car = await _car(session)

    created = await import_and_alert(
        session,
        [PlateScan("01KG999ZZZ", [_violation("AM9")]), PlateScan(car.plate, [_violation("AM1")])],
    )

    assert created == 1, "номер не из парка не импортируется, свой — импортируется"
    opened = await alerts_domain.list_alerts(session, status="open")
    assert [a.car_id for a in opened] == [car.id]
