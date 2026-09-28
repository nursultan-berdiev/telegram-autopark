"""Проверка штрафов по парку и уведомление админов.

Оператору нужен факт нового штрафа и их количество — суммы сервис не отдаёт
вовсе, её смотрят вручную по номеру постановления. Поэтому задача ничего не
домысливает: сообщает, что штраф появился и по какой машине.

Обход парка (`scan_plates`) отделён от работы с БД (`import_and_alert`):
первое тестируется без сети, второе — без браузера. Обе половины общие для
источников: здесь же живёт задача carcheck, задача tolom — в fines_tolom.py.
"""
from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from app.carcheck.browser import open_session
from app.carcheck.parser import parse_violations
from app.fines_sources import Checker, ParsedViolation
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


@dataclass
class PlateScan:
    plate: str
    violations: list[ParsedViolation] = field(default_factory=list)
    unparsed: int = 0
    # Сколько нарушений обещал сам сервис. None — источник итогов не отдаёт,
    # и тогда молчание ответа ничего не доказывает.
    expected: int | None = None


@dataclass
class ScanResult:
    scans: list[PlateScan] = field(default_factory=list)
    refused: list[dict[str, str]] = field(default_factory=list)
    failed: list[dict[str, str]] = field(default_factory=list)
    # Номер, которого нет в реестре сервиса: у нас опечатка в госномере, и
    # штрафы по этой машине не появятся никогда. Это не «проверено и чисто».
    unregistered: list[str] = field(default_factory=list)

    @property
    def checked(self) -> int:
        return len(self.scans)


def _sleep() -> None:
    # Всплеск запросов роняет оценку reCAPTCHA, и сервис начинает отказывать.
    time.sleep(settings.carcheck_pause_seconds + random.uniform(0, 2))


Parser = Callable[[object], tuple[list[ParsedViolation], list[dict]]]
Expected = Callable[[object], int | None]


def scan_plates(
    plates: Iterable[str],
    checker: Checker,
    pause: Callable[[], None] | None = None,
    parse: Parser = parse_violations,
    expected: Expected | None = None,
) -> ScanResult:
    """Обходит номера. На отказе сервиса останавливается, на сбое — продолжает.

    Отказ (капча, блокировка) — это ответ про нас, а не про конкретный номер:
    продолжать перебор бессмысленно и только ухудшит оценку. Сбой одного
    номера остальных не касается.

    Разбор ответа — параметр: обход парка у источников общий, а форма ответа
    у каждого своя.
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
            if not outcome.plate_known:
                # Сервис не знает такого номера. Разобрать по нему нечего, но
                # и промолчать нельзя: иначе опечатка в госномере навсегда
                # выглядит как машина без штрафов.
                result.unregistered.append(plate)
                log.error("номер %s не найден в реестре сервиса", plate)
            parsed, unparsed = parse(outcome.payload)
            result.scans.append(
                PlateScan(
                    plate,
                    parsed,
                    len(unparsed),
                    expected(outcome.payload) if expected else None,
                )
            )
        if index < len(plates) - 1:
            pause()
    return result


def closable_plates(scan: ScanResult) -> dict[str, set[str]]:
    """Номера, по которым молчание сервиса действительно означает «оплачено».

    Не берём:
      * номер вне реестра — там пусто всегда, у нас опечатка в госномере;
      * номер с неразобранными записями — часть нарушений мы не увидели;
      * номер, чьи итоги не сошлись с разобранным или чьих итогов нет вовсе.

    Последнее — единственная защита от тихого переименования контейнера в
    ответе сервиса: разбор вернёт пусто, `unparsed` будет ноль, номер
    останется «известным», и без сверки с итогом оплаченным выглядел бы весь
    парк разом. Номера после отказа сюда не попадают: обход прерывается, и
    `scan_plates` их не собирает.
    """
    unregistered = set(scan.unregistered)
    closable: dict[str, set[str]] = {}
    for plate_scan in scan.scans:
        if plate_scan.plate in unregistered or plate_scan.unparsed:
            continue
        if plate_scan.expected is None or plate_scan.expected != len(
            plate_scan.violations
        ):
            log.warning(
                "%s: итог сервиса (%s) не сошёлся с разобранным (%d) — "
                "пропавшие штрафы не закрываем",
                plate_scan.plate,
                plate_scan.expected,
                len(plate_scan.violations),
            )
            continue
        closable[plate_scan.plate] = {v.external_ref for v in plate_scan.violations}
    return closable


@dataclass
class SyncReport:
    """Итог одного прогона в терминах, которые нужны и журналу, и алерту."""

    created: int = 0
    updated: int = 0
    closed: int = 0
    new_fine_ids: list[int] = field(default_factory=list)
    close_skipped: str | None = None


async def sync_and_alert(
    session: AsyncSession,
    scan: ScanResult,
    *,
    source: str = "carcheck",
    close: bool = False,
    close_limit: int | None = None,
) -> SyncReport:
    """Сводит прогон с базой и уведомляет о действительно новых штрафах.

    Принимает весь `ScanResult`, а не список сканов: право закрывать пропавшие
    зависит от отказов, номеров вне реестра и неразобранных записей — по
    одному списку нарушений этого не видно.

    Импорт делается одним вызовом на прогон: он строит индекс машин и
    водителей, и вызывать его на каждый номер значило бы сканировать обе
    таблицы N раз.
    """
    rows = [
        fines_service.FineImportRow(
            plate=plate_scan.plate,
            external_ref=v.external_ref,
            amount=v.amount,
            amount_to_pay=v.amount_to_pay,
            discount_days_left=v.discount_days_left,
            currency="KGS" if v.amount is not None else None,
            issued_at=v.issued_at,
            article=v.article,
            violation_title=v.violation_title,
            place=v.place,
            payment_code=v.payment_code,
            protocol_kind=v.protocol_kind,
            delivery_date=v.delivery_date,
            note=v.note,
        )
        for plate_scan in scan.scans
        for v in plate_scan.violations
    ]
    # Раннего выхода при пустом списке здесь быть не может: «у машины не
    # осталось ни одного штрафа» — это ровно тот случай, ради которого
    # существует закрытие оплаченных.

    # Импорт, закрытие и уведомление — одна транзакция: иначе упавшее
    # уведомление оставит штраф в базе, и следующий прогон сочтёт его
    # уже известным.
    outcome = await fines_service.import_fines(
        session,
        rows,
        source=source,
        commit=False,
        closable=closable_plates(scan) if close else None,
        close_limit=close_limit,
    )
    report = SyncReport(
        created=outcome.created,
        updated=outcome.updated,
        closed=outcome.closed,
        close_skipped=outcome.close_skipped,
    )
    for plate, fine_ids in outcome.created_ids_per_plate.items():
        car_id = outcome.car_id_per_plate.get(plate)
        if car_id is None:
            # Номер не из парка сюда не попадает: список берётся из БД.
            log.warning("машина %s исчезла между прогоном и импортом", plate)
            continue
        report.new_fine_ids.extend(fine_ids)
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
            # Сами штрафы бот дозапросит по id: класть суммы в payload значило
            # бы хранить их в двух местах и показывать протухшие.
            payload={"new": len(fine_ids), "fine_ids": fine_ids},
            text=f"новых штрафов {len(fine_ids)}:",
        )
    await session.commit()
    return report


def run_payload(scan: ScanResult, report: SyncReport) -> dict[str, Any]:
    """Журнал прогона: и то, что нашли, и то, чего не стали делать."""
    return {
        "plates": len(scan.scans) + len(scan.failed) + len(scan.refused),
        "checked": scan.checked,
        "new": report.created,
        "updated": report.updated,
        "closed": report.closed,
        "new_fine_ids": report.new_fine_ids,
        "close_skipped": report.close_skipped,
        "refused": scan.refused,
        "failed": scan.failed,
        "unregistered": scan.unregistered,
    }


def summarize(scan: ScanResult, report: SyncReport) -> tuple[TaskRunStatus, str]:
    """Отказ сервиса не должен выглядеть как успешная пустая проверка."""
    if scan.refused:
        return TaskRunStatus.refused, f"сервис отказал на {scan.refused[0]['plate']}"
    if scan.failed and scan.checked == 0:
        return TaskRunStatus.failed, "ни один номер не проверен"
    detail = f"проверено {scan.checked}, новых штрафов {report.created}"
    if report.closed:
        detail += f", закрыто оплаченных {report.closed}"
    if report.close_skipped:
        # Отменённое закрытие обязано быть видно: снаружи оно выглядит как
        # «оплаченных не нашлось».
        detail += f"; закрытие пропущено ({report.close_skipped})"
    if scan.unregistered:
        # Не ошибка прогона, но и не норма: номер нужно исправить в карточке.
        detail += f"; нет в реестре: {', '.join(scan.unregistered)}"
    return TaskRunStatus.ok, detail


async def fleet_plates() -> list[str]:
    """Номера парка. Общая для источников: список у них один и тот же."""
    async with session_scope() as session:
        return await cars_service.list_plates(session)


async def _persist(scan: ScanResult) -> SyncReport:
    async with session_scope() as session:
        # Закрытие пропавших выключено: carcheck не отдаёт итогов, а без них
        # пустой ответ неотличим от молча изменившейся формы (см. closable_plates).
        return await sync_and_alert(session, scan, source="carcheck", close=False)


@celery_app.task(name=NAME)
def check_fines(periodic_task_id: int | None = None) -> dict[str, int]:
    scan = ScanResult()
    report = SyncReport()
    try:
        plates = run_async(fleet_plates)
        with record_run(NAME, periodic_task_id) as run:
            with open_session() as checker:
                scan = scan_plates(plates, checker)
            report = run_async(lambda: _persist(scan))
            status, detail = summarize(scan, report)
            run.status = status
            run.detail = detail
            run.payload = run_payload(scan, report)
    finally:
        # Именно в finally: поломка, которая воспроизводится каждый прогон
        # (не встал Chromium, недоступна БД), иначе не дала бы предупредить
        # ни разу — исключение уходило бы мимо проверки серии отказов.
        run_async(lambda: warn_on_failure_streak(NAME, "carcheck.gov.kg"))
    return {"checked": scan.checked, "new": report.created}


async def warn_on_failure_streak(task_name: str, source: str) -> None:
    """Серия отказов подряд — повод сказать людям, а не молча ждать.

    Общая для источников: отказ снаружи неотличим от «штрафов нет», и порог
    тревоги у обоих один.
    """
    async with session_scope() as session:
        streak = await periodic_service.consecutive_failures(session, task_name)
    if streak >= settings.fines_failure_alert_after:
        log.error(
            "проверка штрафов на %s не проходит %d раз подряд", source, streak
        )
