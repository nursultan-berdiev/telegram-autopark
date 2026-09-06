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

from app.db.models import TaskRun, TaskRunStatus
from app.db.sync_engine import sync_engine

# Драйверы охотно вкладывают строку подключения в текст ошибки, а журнал
# прогонов читают через API — пароль там не нужен.
_DSN = re.compile(r"\b\w+(?:\+\w+)?://[^\s'\"]+")


def _safe_detail(exc: BaseException) -> str:
    return _DSN.sub("<dsn>", f"{type(exc).__name__}: {exc}")[:2000]


@contextmanager
def record_run(task: str, periodic_task_id: int | None = None) -> Iterator[TaskRun]:
    """Открывает запись о прогоне и закрывает её любым исходом.

    Незаписанный прогон — это молчаливый отказ: снаружи он неотличим от
    успешного и пустого, и парк копил бы штрафы незаметно.
    """
    with Session(sync_engine()) as session:
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
            run.finished_at = datetime.now(timezone.utc)
            session.add(run)
            session.commit()
