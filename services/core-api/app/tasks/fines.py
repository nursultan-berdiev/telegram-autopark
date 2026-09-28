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
from decimal import Decimal
from typing import Callable, Iterable, Protocol, Sequence

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


def scan_plates(
    plates: Iterable[str],
    checker: Checker,
    pause: Callable[[], None] | None = None,
    parse: Parser = parse_violations,
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
            result.scans.append(PlateScan(plate, parsed, len(unparsed)))
        if index < len(plates) - 1:
            pause()
    return result


class HasAmounts(Protocol):
    """Что нужно от штрафа для итоговой суммы.

    Без контракта опечатка в имени поля молча превращалась бы в `None`, то
    есть в «сумма неизвестна» — ровно в ту цифру, ради которой всё считается.
    """

    amount: Decimal | None
    amount_to_pay: Decimal | None


def money_summary(fines: Sequence[HasAmounts]) -> str:
    """Сумма по неоплаченным штрафам, честно помечая неизвестное.

    У carcheck и ручного ввода суммы может не быть вовсе, поэтому «на 600
    сом» и «не менее 600 сом» — разные утверждения, и подменять второе
    первым нельзя: оператор примет неполную цифру за полную.
    """
    known = [f for f in fines if f.amount is not None]
    if not known:
        return ""
    total = sum((Decimal(str(f.amount)) for f in known), Decimal(0))
    prefix = " на " if len(known) == len(fines) else " не менее чем на "
    text = f"{prefix}{_money(total)} сом"

    # Скидка есть не у каждого штрафа: складываем к оплате по всем известным,
    # подставляя полную сумму там, где скидки нет.
    to_pay = sum(
        (
            Decimal(str(f.amount_to_pay if f.amount_to_pay is not None else f.amount))
            for f in known
        ),
        Decimal(0),
    )
    if to_pay != total:
        text += f" (со скидкой {_money(to_pay)} сом)"
    return text


def _money(value: Decimal) -> str:
    """Целые суммы — без копеечного хвоста: так их печатает и сам сервис."""
    quantized = value.normalize()
    return str(quantized.quantize(Decimal(1)) if quantized == quantized.to_integral() else quantized)


async def import_and_alert(
    session: AsyncSession,
    scans: Sequence[PlateScan],
    *,
    source: str,
    hint: str,
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
            amount_to_pay=v.amount_to_pay,
            discount_days_left=v.discount_days_left,
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
        session, rows, source=source, commit=False
    )
    for plate, created in outcome.created_per_plate.items():
        car_id = outcome.car_id_per_plate.get(plate)
        if car_id is None:
            # Номер не из парка сюда не попадает: список берётся из БД.
            log.warning("машина %s исчезла между прогоном и импортом", plate)
            continue
        unpaid_fines = await fines_service.list_fines(session, car_id, only_unpaid=True)
        unpaid = len(unpaid_fines)
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
                f"новых штрафов: {created}, "
                f"всего неоплаченных: {unpaid}{money_summary(unpaid_fines)}. "
                f"{hint}"
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
    detail = f"проверено {scan.checked}, новых штрафов {new_total}"
    if scan.unregistered:
        # Не ошибка прогона, но и не норма: номер нужно исправить в карточке.
        detail += f"; нет в реестре: {', '.join(scan.unregistered)}"
    return TaskRunStatus.ok, detail


async def fleet_plates() -> list[str]:
    """Номера парка. Общая для источников: список у них один и тот же."""
    async with session_scope() as session:
        return await cars_service.list_plates(session)


async def _persist(scan: ScanResult) -> int:
    async with session_scope() as session:
        return await import_and_alert(
            session,
            scan.scans,
            source="carcheck",
            hint="Сумму смотрите на carcheck.gov.kg по номеру постановления",
        )


@celery_app.task(name=NAME)
def check_fines(periodic_task_id: int | None = None) -> dict[str, int]:
    scan = ScanResult()
    new_total = 0
    try:
        plates = run_async(fleet_plates)
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
                "unregistered": scan.unregistered,
            }
    finally:
        # Именно в finally: поломка, которая воспроизводится каждый прогон
        # (не встал Chromium, недоступна БД), иначе не дала бы предупредить
        # ни разу — исключение уходило бы мимо проверки серии отказов.
        run_async(lambda: warn_on_failure_streak(NAME, "carcheck.gov.kg"))
    return {"checked": scan.checked, "new": new_total}


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
