"""Роутер schedules: график платежей водителя (см. plan/03)."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor, require_core
from app.db.models import PaymentSchedule, SchedulePeriod
from app.db.session import get_session
from app.domain import drivers as drivers_service
from app.domain import schedules as sched
from app.errors import DomainError, NotFound
from contracts import ScheduleDTO, ScheduleStatusDTO, ScheduleUpsert, ScheduleWithStatus

router = APIRouter()


def _utc(dt: datetime | None) -> datetime | None:
    # SQLite в тестах отдаёт naive datetime — приводим к UTC для контракта.
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _schedule_dto(schedule: PaymentSchedule) -> ScheduleDTO:
    return ScheduleDTO(
        id=schedule.id,
        driver_id=schedule.driver_id,
        period=schedule.period.value,
        interval_days=schedule.interval_days,
        amount=schedule.amount,
        paid_in_period=schedule.paid_in_period,
        next_due_date=_utc(schedule.next_due_date),
        active=schedule.active,
    )


def _status_dto(schedule: PaymentSchedule) -> ScheduleStatusDTO:
    # schedule_status/due_summary/period_label — расчёт из домена, не переписываем.
    st = sched.schedule_status(schedule)
    return ScheduleStatusDTO(
        next_due_date=st.next_due_date,
        amount=st.amount,
        paid_in_period=st.paid_in_period,
        remaining_current=st.remaining_current,
        overdue_periods=st.overdue_periods,
        overdue_days=st.overdue_days,
        debt_now=st.debt_now,
        is_overdue=st.is_overdue,
        summary=sched.due_summary(st),
        period_label=sched.period_label(schedule.period, schedule.interval_days),
    )


@router.get("/drivers/{driver_id}/schedule", response_model=ScheduleWithStatus)
async def get_driver_schedule(
    driver_id: int,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> ScheduleWithStatus:
    schedule = await sched.get_schedule(session, driver_id)
    if schedule is None:
        return ScheduleWithStatus(schedule=None, status=None)
    return ScheduleWithStatus(schedule=_schedule_dto(schedule), status=_status_dto(schedule))


@router.put("/drivers/{driver_id}/schedule", response_model=ScheduleWithStatus)
async def put_driver_schedule(
    driver_id: int,
    payload: ScheduleUpsert,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> ScheduleWithStatus:
    driver = await drivers_service.get_driver(session, driver_id)
    if driver is None:
        raise NotFound(f"водитель {driver_id} не найден")

    try:
        period = SchedulePeriod(payload.period)
    except ValueError:
        raise DomainError(f"неизвестная периодичность: {payload.period}", status_code=422)
    if period is SchedulePeriod.custom and not payload.interval_days:
        raise DomainError("для period=custom нужен interval_days", status_code=422)

    next_due = payload.next_due_date
    if next_due.tzinfo is None:
        next_due = next_due.replace(tzinfo=timezone.utc)

    schedule = await sched.set_schedule(
        session,
        driver_id=driver_id,
        period=period,
        interval_days=payload.interval_days,
        amount=payload.amount,
        next_due_date=next_due,
    )
    return ScheduleWithStatus(schedule=_schedule_dto(schedule), status=_status_dto(schedule))
