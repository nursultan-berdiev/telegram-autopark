"""Проверка штрафов по парку и уведомление админов.

Оператору нужен факт нового штрафа и их количество — суммы сервис не отдаёт
вовсе, её смотрят вручную по номеру постановления. Поэтому задача ничего не
домысливает: сообщает, что штраф появился и по какой машине.

Обход парка (`scan_plates`) отделён от работы с БД (`import_and_alert`):
первое тестируется без сети, второе — без браузера.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Callable, Iterable, Protocol, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from app.carcheck.browser import CheckResult, open_session
from app.carcheck.parser import ParsedViolation, parse_violations
from app.config import settings
from app.db.models import AlertType, TaskRunStatus
from app.domain import alerts as alerts_domain
from app.domain import cars as cars_service
from app.domain import fines as fines_service
from app.domain import periodic as periodic_service
from app.tasks.asyncio_bridge import run_async, session_scope
from app.tasks.celery_app import celery_app
from app.tasks.runlog import record_run

log = logging.getLogger(__name__)

NAME = "app.tasks.fines.check_fines"


class Checker(Protocol):
    def check(self, plate: str) -> CheckResult: ...


@dataclass
class PlateScan:
    plate: str
    violations: list[ParsedViolation] = field(default_factory=list)
    unparsed: int = 0


@dataclass
class ScanResult:
    scans: list[PlateScan] = field(default_factory=list)
    refused: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)

    @property
    def checked(self) -> int:
        return len(self.scans)


def _sleep() -> None:
    # Всплеск запросов роняет оценку reCAPTCHA, и сервис начинает отказывать.
    time.sleep(settings.carcheck_pause_seconds + random.uniform(0, 2))


def scan_plates(
    plates: Iterable[str], checker: Checker, pause: Callable[[], None] | None = None
) -> ScanResult:
    """Обходит номера. На отказе сервиса останавливается, на сбое — продолжает.

    Отказ (капча, блокировка) — это ответ про нас, а не про конкретный номер:
    продолжать перебор бессмысленно и только ухудшит оценку. Сбой одного
    номера остальных не касается.
    """
    pause = pause or _sleep
    plates = list(plates)
    result = ScanResult()
    for index, plate in enumerate(plates):
        outcome = checker.check(plate)
        if outcome.refused is not None:
            result.refused.append({"plate": plate, "reason": outcome.refused})
            break
        if not outcome.ok:
            result.failed.append({"plate": plate, "error": outcome.error})
        else:
            parsed, unparsed = parse_violations(outcome.payload)
            result.scans.append(PlateScan(plate, parsed, len(unparsed)))
        if index < len(plates) - 1:
            pause()
    return result


async def import_and_alert(
    session: AsyncSession, scans: Sequence[PlateScan]
) -> int:
    """Заводит найденные штрафы одной пачкой и уведомляет о действительно новых.

    Импорт делается одним вызовом на весь прогон: он строит индекс машин и
    водителей, и вызывать его на каждый номер значило бы сканировать обе
    таблицы N раз.
    """
    rows = [
        fines_service.FineImportRow(
            plate=scan.plate,
            external_ref=v.external_ref,
            amount=v.amount,
            currency="KGS" if v.amount is not None else None,
            issued_at=v.issued_at,
            note=v.note,
        )
        for scan in scans
        for v in scan.violations
    ]
    if not rows:
        return 0

    # Импорт и уведомление — одна транзакция: иначе упавшее уведомление
    # оставит штраф в базе, и следующий прогон сочтёт его уже известным.
    outcome = await fines_service.import_fines(
        session, rows, source="carcheck", commit=False
    )
    for plate, created in outcome.created_per_plate.items():
        car_id = outcome.car_id_per_plate.get(plate)
        if car_id is None:
            # Номер не из парка сюда не попадает: список берётся из БД.
            log.warning("машина %s исчезла между прогоном и импортом", plate)
            continue
        unpaid = len(await fines_service.list_fines(session, car_id, only_unpaid=True))
        # Прошлый алерт закрываем принудительно: raise_alert перезаписал бы
        # payload открытого, а бот дедуплицирует доставку по id — вторая
        # партия штрафов не дошла бы до оператора никогда.
        await alerts_domain.resolve_open(
            session, car_id=car_id, atype=AlertType.new_fine
        )
        await alerts_domain.raise_alert(
            session,
            car_id=car_id,
            atype=AlertType.new_fine,
            payload={"new": created, "unpaid_total": unpaid},
            text=(
                f"новых штрафов: {created}, всего неоплаченных: {unpaid}. "
                "Сумму смотрите на carcheck.gov.kg по номеру постановления"
            ),
        )
    await session.commit()
    return outcome.created


def summarize(scan: ScanResult, new_total: int) -> tuple[TaskRunStatus, str]:
    """Отказ сервиса не должен выглядеть как успешная пустая проверка."""
    if scan.refused:
        return TaskRunStatus.refused, f"сервис отказал на {scan.refused[0]['plate']}"
    if scan.failed and scan.checked == 0:
        return TaskRunStatus.failed, "ни один номер не проверен"
    return TaskRunStatus.ok, f"проверено {scan.checked}, новых штрафов {new_total}"


async def _plates() -> list[str]:
    async with session_scope() as session:
        return await cars_service.list_plates(session)


async def _persist(scan: ScanResult) -> int:
    async with session_scope() as session:
        return await import_and_alert(session, scan.scans)


@celery_app.task(name=NAME)
def check_fines(periodic_task_id: int | None = None) -> dict[str, int]:
    scan = ScanResult()
    new_total = 0
    try:
        plates = run_async(_plates)
        with record_run(NAME, periodic_task_id) as run:
            with open_session() as checker:
                scan = scan_plates(plates, checker)
            new_total = run_async(lambda: _persist(scan))
            status, detail = summarize(scan, new_total)
            run.status = status
            run.detail = detail
            run.payload = {
                "plates": len(plates),
                "checked": scan.checked,
                "new": new_total,
                "refused": scan.refused,
                "failed": scan.failed,
            }
    finally:
        # Именно в finally: поломка, которая воспроизводится каждый прогон
        # (не встал Chromium, недоступна БД), иначе не дала бы предупредить
        # ни разу — исключение уходило бы мимо проверки серии отказов.
        run_async(_warn_on_failure_streak)
    return {"checked": scan.checked, "new": new_total}


async def _warn_on_failure_streak() -> None:
    """Серия отказов подряд — повод сказать людям, а не молча ждать."""
    async with session_scope() as session:
        streak = await periodic_service.consecutive_failures(session, NAME)
    if streak >= settings.carcheck_failure_alert_after:
        log.error("проверка штрафов не проходит %d раз подряд", streak)
