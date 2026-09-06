"""Роутер расписаний фоновых задач: их правит админка, а не деплой."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor
from app.db.models import PeriodicTask, TaskRun
from app.db.session import get_session
from app.domain import periodic as periodic_service
from app.errors import NotFound
from contracts import (
    PeriodicTaskDTO,
    PeriodicTaskPatch,
    PeriodicTaskUpsert,
    TaskRunDTO,
)

router = APIRouter()


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


def _run_dto(row: TaskRun) -> TaskRunDTO:
    return TaskRunDTO(
        id=row.id,
        task=row.task,
        periodic_task_id=row.periodic_task_id,
        status=row.status.value,
        started_at=_utc(row.started_at),  # type: ignore[arg-type]
        finished_at=_utc(row.finished_at),
        detail=row.detail,
        payload=row.payload,
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


@router.get("/task-runs", response_model=list[TaskRunDTO])
async def list_task_runs(
    task: str | None = None,
    limit: int = 50,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> list[TaskRunDTO]:
    runs = await periodic_service.list_runs(session, task=task, limit=min(limit, 200))
    return [_run_dto(r) for r in runs]
