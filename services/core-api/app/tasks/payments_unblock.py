"""Авто-разблокировка двигателя при оплате — периодическая задача.

Часто (каждые ~2 мин, interval в строке periodic_tasks) снимает СИСТЕМНУЮ
блокировку с машин, чьи водители закрыли долг: оплативший не должен стоять до
прихода админа. Ручной блок админа не трогается. Политика — в
`app.domain.engine_enforcement`; здесь только обёртка задачи.

Отдельная задача от `block_overdue`: выключается независимо (как просил заказчик),
оставив авто-блокировку работать.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone

from app.db.models import CommandSource, TaskRunStatus
from app.domain import engine_enforcement as enforcement
from app.tasks.asyncio_bridge import run_async, session_scope
from app.tasks.celery_app import celery_app
from app.tasks.runlog import record_run

log = logging.getLogger(__name__)

NAME = "app.tasks.payments_unblock.unblock_paid"


async def _run() -> dict[str, int]:
    """Снимает системный блок с оплативших; commit и изоляция ошибок — по машине.

    `engine_resume` на реле уже ушёл — откатывать строки Command/алерты нельзя
    (иначе повторная отправка без уведомления), поэтому commit на каждую машину, а
    сбои — в счётчик `errors`. Возвращает Counter исходов.
    """
    now = datetime.now(timezone.utc)
    outcomes: Counter[str] = Counter()
    async with session_scope() as session:
        car_ids = await enforcement.cars_under_system_block(session, CommandSource.overdue)
        for car_id in car_ids:
            try:
                outcome = await enforcement.release_if_paid(
                    session, car_id=car_id, now=now
                )
                await session.commit()
                outcomes[outcome.value] += 1
            except Exception:  # noqa: BLE001 — одна машина не должна ронять остальных
                await session.rollback()
                log.exception("unblock_paid: сбой на машине %s", car_id)
                outcomes["errors"] += 1
    return dict(outcomes)


@celery_app.task(name=NAME)
def unblock_paid(
    periodic_task_id: int | None = None, run_id: int | None = None
) -> dict[str, int]:
    """Celery-задача: снятие системной блокировки после оплаты.

    Побочные эффекты: шлёт engine_resume, поднимает overdue_unblock/_failed, пишет
    прогон в task_runs. Ручной блок админа не трогает. Возвращает Counter исходов.
    """
    with record_run(NAME, periodic_task_id, run_id=run_id) as run:
        outcomes = run_async(_run)
        run.status = TaskRunStatus.ok
        run.detail = (
            f"под блоком {sum(outcomes.values())}, "
            f"разблокировано {outcomes.get('unblocked', 0)}"
        )
        run.payload = outcomes
    log.info("unblock_paid: %s", outcomes)
    return outcomes
