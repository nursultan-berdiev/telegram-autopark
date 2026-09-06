"""Проверка штрафов на tolom.kg — основной источник.

Отличие от carcheck одно, но решающее: сервис отдаёт суммы, статью и название
нарушения, и делает это обычным HTTP — без браузера и без reCAPTCHA. Поэтому
задача дешевле по ресурсам и не зависит от оценки капчи, а carcheck остаётся
запасным путём (plan/10).

Механика обхода парка, импорта и уведомления — общая, из `app.tasks.fines`:
разные здесь только клиент и разбор ответа.
"""
from __future__ import annotations

import logging
import random
import time

from app.config import settings
from app.tasks.asyncio_bridge import run_async, session_scope
from app.tasks.celery_app import celery_app
from app.tasks.fines import (
    ScanResult,
    fleet_plates,
    import_and_alert,
    scan_plates,
    summarize,
    warn_on_failure_streak,
)
from app.tasks.runlog import record_run
from app.tolom.client import open_session
from app.tolom.parser import parse_violations

log = logging.getLogger(__name__)

NAME = "app.tasks.fines_tolom.check_fines_tolom"
SOURCE = "tolom"
HINT = "Подробности и оплата — на tolom.kg по номеру постановления"


def _sleep() -> None:
    # Браузера нет и капчи нет, но частота запросов к госсервису — вопрос
    # приличия: пауза короче carcheck'овой, но не нулевая.
    time.sleep(settings.tolom_pause_seconds + random.uniform(0, 1))


async def _persist(scan: ScanResult) -> int:
    async with session_scope() as session:
        return await import_and_alert(
            session, scan.scans, source=SOURCE, hint=HINT
        )


@celery_app.task(name=NAME)
def check_fines_tolom(periodic_task_id: int | None = None) -> dict[str, int]:
    scan = ScanResult()
    new_total = 0
    try:
        plates = run_async(fleet_plates)
        with record_run(NAME, periodic_task_id) as run:
            with open_session() as checker:
                scan = scan_plates(
                    plates, checker, pause=_sleep, parse=parse_violations
                )
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
        # Как и у carcheck — в finally: поломка, воспроизводящаяся каждый
        # прогон, иначе не дала бы предупредить ни разу.
        run_async(lambda: warn_on_failure_streak(NAME, "tolom.kg"))
    return {"checked": scan.checked, "new": new_total}
