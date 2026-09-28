"""Запись прогонов задач.

Отдельный синхронный модуль: celery работает вне event loop, а домен
core-api асинхронный. Дублируется здесь только запись журнала — всё
остальное задачи делают через обычный домен.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Iterator

from sqlalchemy.orm import Session

from app.db.models import PeriodicTask, TaskRun, TaskRunStatus
from app.db.sync_engine import sync_engine

# Драйверы охотно вкладывают строку подключения в текст ошибки, а журнал
# прогонов читают через API — пароль там не нужен.
_DSN = re.compile(r"\b\w+(?:\+\w+)?://[^\s'\"]+")


def _safe_detail(exc: BaseException) -> str:
    return _DSN.sub("<dsn>", f"{type(exc).__name__}: {exc}")[:2000]


@contextmanager
def record_run(
    task: str, periodic_task_id: int | None = None, run_id: int | None = None
) -> Iterator[TaskRun]:
    """Открывает запись о прогоне и закрывает её любым исходом.

    Незаписанный прогон — это молчаливый отказ: снаружи он неотличим от
    успешного и пустого, и парк копил бы штрафы незаметно.

    `run_id` подхватывает строку, заведённую при постановке в очередь: кнопка
    «Проверить сейчас» ждёт результат именно своего прогона, а по времени
    старта его не отличить от кронового, начавшегося в ту же секунду.
    """
    with Session(sync_engine()) as session:
        run = session.get(TaskRun, run_id) if run_id is not None else None
        if run is None:
            run = TaskRun(
                task=task,
                periodic_task_id=periodic_task_id,
                status=TaskRunStatus.failed,
                started_at=datetime.now(timezone.utc),
            )
            session.add(run)
            session.commit()
        try:
            yield run
        except Exception as exc:
            run.status = TaskRunStatus.failed
            run.detail = _safe_detail(exc)
            raise
        finally:
            finished = datetime.now(timezone.utc)
            run.finished_at = finished
            session.add(run)
            if periodic_task_id is not None:
                # Счётчик обновляет исполнитель, а не планировщик: иначе
                # запуск из админки не отражается в строке расписания —
                # прогон в журнале есть, а «последний запуск» пуст.
                row = session.get(PeriodicTask, periodic_task_id)
                if row is not None:
                    row.last_run_at = finished
                    row.total_run_count = (row.total_run_count or 0) + 1
            session.commit()
