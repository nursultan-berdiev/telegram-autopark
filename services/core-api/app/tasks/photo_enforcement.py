"""ЗАДЕЛ: авто-блокировка двигателя за пропуск фотоконтроля. Не включена.

Решение о блокировке за фотоконтроль — за владельцем. Замков три: модуль не
подключён к `celery_app.include` (воркер задачу не знает), не внесён в `KNOWN_TASKS`
админки, и сама задача ничего не делает без `PHOTO_AUTOBLOCK_ENABLED=1`. Включение
— все три шага плюс строка periodic_tasks с `args.min_overdue_days` (см. plan/06).

Механика — общая с блокировкой за аренду и штрафы (`engine_enforcement`):
взвод до безопасной остановки, снятие только своего блока (`source=photo`).
Выключатель останавливает и снятие: блоки `source=photo`, взведённые до выключения,
сами не разблокируются — их снимает админ кнопкой «Разблокировать».
"""
from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone

from app.config import settings
from app.db.models import CommandSource, TaskRunStatus
from app.domain import engine_enforcement as enforcement
from app.domain import photo_inspections as photo_domain
from app.tasks.asyncio_bridge import run_async, session_scope
from app.tasks.celery_app import celery_app
from app.tasks.runlog import record_run

log = logging.getLogger(__name__)

NAME = "app.tasks.photo_enforcement.enforce_photo_inspections"
# Сколько дней просрочки терпим до блокировки, если в args не задано иное.
DEFAULT_MIN_OVERDUE_DAYS = 2


async def _run(min_overdue_days: int) -> dict[str, int]:
    """Взводит блок машинам с просрочкой ≥ N дней и снимает у сдавших.

    commit и изоляция ошибок — по машине, как в fines_enforcement. Без выключателя
    не делает ничего; N < 1 — ошибка настройки (блок в день срока), а не «блокируй всех».
    """
    if not settings.photo_autoblock_enabled:
        return {"disabled": 1}
    if min_overdue_days < 1:
        raise ValueError(f"min_overdue_days должен быть ≥ 1, получено {min_overdue_days}")
    now = datetime.now(timezone.utc)
    policy = enforcement.photo_policy(min_overdue_days)
    outcomes: Counter[str] = Counter()
    async with session_scope() as session:
        candidates = [
            s.car_id
            for s in await photo_domain.statuses(session, now)
            if s.overdue_days >= min_overdue_days
        ]
        for car_id in candidates:
            try:
                outcome = await enforcement.enforce_block(
                    session, car_id=car_id, policy=policy, now=now
                )
                await session.commit()
                outcomes[f"block-{outcome.value}"] += 1
            except Exception:  # noqa: BLE001 — одна машина не роняет остальных
                await session.rollback()
                log.exception("enforce_photo: сбой блокировки машины %s", car_id)
                outcomes["errors"] += 1
        for car_id in await enforcement.cars_under_system_block(session, CommandSource.photo):
            try:
                outcome = await enforcement.release_if_cleared(
                    session, car_id=car_id, policy=policy, now=now
                )
                await session.commit()
                outcomes[f"release-{outcome.value}"] += 1
            except Exception:  # noqa: BLE001
                await session.rollback()
                log.exception("enforce_photo: сбой разблокировки машины %s", car_id)
                outcomes["errors"] += 1
    return dict(outcomes)


@celery_app.task(name=NAME)
def enforce_photo_inspections(
    periodic_task_id: int | None = None,
    run_id: int | None = None,
    min_overdue_days: int | None = None,
) -> dict[str, int]:
    """Celery-задача: блокировка/разблокировка за пропуск фотоконтроля (задел).

    Без `PHOTO_AUTOBLOCK_ENABLED=1` ничего не делает и возвращает `{"disabled": 1}`.
    Порог `min_overdue_days` — из args строки periodic_tasks (по умолчанию
    DEFAULT_MIN_OVERDUE_DAYS); меньше 1 — `ValueError`, прогон уходит в журнал сбоем.
    Иначе возвращает Counter исходов с префиксами block-/release-.
    """
    n = min_overdue_days if min_overdue_days is not None else DEFAULT_MIN_OVERDUE_DAYS
    with record_run(NAME, periodic_task_id, run_id=run_id) as run:
        outcomes = run_async(lambda: _run(n))
        run.status = TaskRunStatus.ok
        run.detail = (
            "выключено (PHOTO_AUTOBLOCK_ENABLED=0)"
            if outcomes.get("disabled")
            else f"просрочка от {n} дн., взведено {outcomes.get('block-armed', 0)}, "
            f"разблокировано {outcomes.get('release-unblocked', 0)}"
        )
        run.payload = outcomes
    log.info("enforce_photo_inspections(min_overdue_days=%s): %s", n, outcomes)
    return outcomes
