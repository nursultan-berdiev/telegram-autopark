"""Роутер расписаний фоновых задач: их правит админка, а не деплой."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor
from app.db.models import PeriodicTask, TaskRun
from app.db.session import get_session
from app.domain import periodic as periodic_service
from app.errors import NotFound
from app.tasks.catalog import KNOWN_TASKS
from contracts import (
    PeriodicTaskDTO,
    PeriodicTaskPatch,
    PeriodicTaskUpsert,
    TaskRunDTO,
)

log = logging.getLogger(__name__)
router = APIRouter()


@router.get("/periodic-tasks/catalog")
async def task_catalog(
    _: int = Depends(require_admin_actor),
) -> dict[str, str]:
    """Имя задачи → человеческая подпись: наполняет выпадашку формы в SPA."""
    return KNOWN_TASKS


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite в тестах отдаёт naive datetime — приводим к UTC для контракта.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _task_dto(row: PeriodicTask) -> PeriodicTaskDTO:
    return PeriodicTaskDTO(
        id=row.id,
        name=row.name,
        task=row.task,
        interval_seconds=row.interval_seconds,
        crontab=row.crontab,
        args=row.args,
        enabled=row.enabled,
        last_run_at=_utc(row.last_run_at),
        total_run_count=row.total_run_count,
        created_at=_utc(row.created_at),
    )


def run_dto(row: TaskRun) -> TaskRunDTO:
    return TaskRunDTO(
        id=row.id,
        task=row.task,
        periodic_task_id=row.periodic_task_id,
        status=row.status.value,
        started_at=_utc(row.started_at),  # type: ignore[arg-type]
        finished_at=_utc(row.finished_at),
        detail=row.detail,
        payload=row.payload,
        requested_by=row.requested_by,
    )


@router.get("/periodic-tasks", response_model=list[PeriodicTaskDTO])
async def list_periodic_tasks(
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> list[PeriodicTaskDTO]:
    return [_task_dto(r) for r in await periodic_service.list_tasks(session)]


@router.post("/periodic-tasks", response_model=PeriodicTaskDTO, status_code=201)
async def create_periodic_task(
    payload: PeriodicTaskUpsert,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> PeriodicTaskDTO:
    # Имя задачи — только из каталога: иначе форма в браузере поставила бы в
    # очередь произвольную точку входа. Старая Jinja-форма проверяла это же.
    if payload.task not in KNOWN_TASKS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"неизвестная задача: {payload.task}",
        )
    row = await periodic_service.create_task(
        session,
        name=payload.name,
        task=payload.task,
        interval_seconds=payload.interval_seconds,
        crontab=payload.crontab,
        args=payload.args,
        enabled=payload.enabled,
    )
    return _task_dto(row)


@router.patch("/periodic-tasks/{task_id}", response_model=PeriodicTaskDTO)
async def patch_periodic_task(
    task_id: int,
    payload: PeriodicTaskPatch,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> PeriodicTaskDTO:
    changes = payload.model_dump(exclude_unset=True)
    if "task" in changes and changes["task"] not in KNOWN_TASKS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"неизвестная задача: {changes['task']}",
        )
    row = await periodic_service.update_task(session, task_id, **changes)
    if row is None:
        raise NotFound(f"расписание {task_id} не найдено")
    return _task_dto(row)


@router.delete("/periodic-tasks/{task_id}", status_code=204, response_model=None)
async def delete_periodic_task(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> None:
    if not await periodic_service.delete_task(session, task_id):
        raise NotFound(f"расписание {task_id} не найдено")


@router.post("/periodic-tasks/{task_id}/run", status_code=status.HTTP_202_ACCEPTED)
async def run_now(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> dict[str, str]:
    """«Запустить сейчас»: ставит задачу в очередь немедленно, не трогая
    расписание. Прогон появится в журнале, как у планового запуска."""
    row = await periodic_service.get_task(session, task_id)
    if row is None:
        raise NotFound(f"расписание {task_id} не найдено")
    from app.tasks.celery_app import celery_app

    try:
        # retry=False: недоступный брокер отвечает сразу, а не копит попытки.
        celery_app.send_task(row.task, kwargs={"periodic_task_id": row.id}, retry=False)
    except Exception as exc:  # noqa: BLE001 — брокер лёг, это и есть ответ клиенту
        log.warning("задача %s не поставлена в очередь", row.task, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"очередь недоступна: {type(exc).__name__}",
        ) from exc
    return {"task": row.task}


@router.get("/task-runs/{run_id}", response_model=TaskRunDTO)
async def get_task_run(
    run_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> TaskRunDTO:
    """Один прогон: за ним следит кнопка «Проверить сейчас» в боте."""
    run = await periodic_service.get_run(session, run_id)
    if run is None:
        raise NotFound("прогон не найден")
    return run_dto(run)


@router.get("/task-runs", response_model=list[TaskRunDTO])
async def list_task_runs(
    task: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> list[TaskRunDTO]:
    runs = await periodic_service.list_runs(session, task=task, limit=min(limit, 200))
    return [run_dto(r) for r in runs]
