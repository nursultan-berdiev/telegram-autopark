"""Авто-блокировка двигателя за неоплату — периодическая задача.

Раз в сутки (22:00 Бишкек, cron в строке periodic_tasks) взводит блокировку
машинам просроченных водителей. Блок именно ВЗВОДИТСЯ (`always_arm`): едущая/офлайн
машина не глохнет мгновенно, а заглохнет сама на первой безопасной остановке через
`fire_armed`; тогда же придут уведомления админу и водителю. Политика — в
`app.domain.overdue_enforcement`; здесь только обёртка задачи.

Задача включается/выключается независимо — своей строкой в `periodic_tasks`.
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import settings
from app.db.models import TaskRunStatus
from app.domain import overdue_enforcement as enforcement
from app.tasks.asyncio_bridge import run_async, session_scope
from app.tasks.celery_app import celery_app
from app.tasks.runlog import record_run

log = logging.getLogger(__name__)

NAME = "app.tasks.payments_block.block_overdue"


async def _run() -> dict[str, int]:
    """Взводит блок всем просроченным; commit и изоляция ошибок — по машине.

    Падение на одной машине не должно оставлять без блокировки остальных должников
    и путаться с «никого не просрочено», поэтому commit на каждую, а сбои — в
    счётчик `errors`. «Сейчас» берём в Бишкеке: срок в графике хранится с зоной, а
    `is_overdue` считается относительно переданного now. Возвращает Counter исходов.
    """
    now = datetime.now(ZoneInfo(settings.timezone))
    outcomes: Counter[str] = Counter()
    async with session_scope() as session:
        car_ids = await enforcement.overdue_car_ids(session, now)
        for car_id in car_ids:
            try:
                outcome = await enforcement.enforce_overdue_block(
                    session, car_id=car_id, now=now
                )
                await session.commit()
                outcomes[outcome.value] += 1
            except Exception:  # noqa: BLE001 — одна машина не должна ронять остальных
                await session.rollback()
                log.exception("block_overdue: сбой на машине %s", car_id)
                outcomes["errors"] += 1
    return dict(outcomes)


@celery_app.task(name=NAME)
def block_overdue(
    periodic_task_id: int | None = None, run_id: int | None = None
) -> dict[str, int]:
    """Celery-задача: блокировка двигателя неоплативших к сроку.

    Побочные эффекты: взводит системные блоки (`source=overdue`), пишет прогон в
    task_runs (`record_run`). Возвращает Counter исходов по машинам.
    """
    with record_run(NAME, periodic_task_id, run_id=run_id) as run:
        outcomes = run_async(_run)
        run.status = TaskRunStatus.ok
        run.detail = (
            f"просрочено {sum(outcomes.values())}, взведено {outcomes.get('armed', 0)}"
        )
        run.payload = outcomes
    log.info("block_overdue: %s", outcomes)
    return outcomes
