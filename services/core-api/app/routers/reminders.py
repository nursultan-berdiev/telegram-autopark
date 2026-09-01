"""Роутер reminders: план для бота (бот тянет по своему cron) + антиспам-отметка."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_core
from app.config import settings
from app.db.models import PaymentSchedule
from app.db.session import get_session
from app.domain import reminders as reminders_domain
from contracts import ReminderItem, ReminderMark, ReminderPlanDTO

router = APIRouter()


@router.get("/plan", response_model=ReminderPlanDTO)
async def plan(
    force: bool = False,
    now: datetime | None = Query(default=None),
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> ReminderPlanDTO:
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    result = await reminders_domain.collect(
        session, now, settings.timezone, force=force
    )

    # DriverReminder не хранит driver_id — достаём одним запросом по schedule_id
    # (schedule_id -> driver_id взаимно однозначны: у графика один водитель).
    schedule_ids = [r.schedule_id for r in result.reminders]
    driver_by_schedule: dict[int, int] = {}
    if schedule_ids:
        rows = await session.execute(
            select(PaymentSchedule.id, PaymentSchedule.driver_id).where(
                PaymentSchedule.id.in_(schedule_ids)
            )
        )
        driver_by_schedule = dict(rows.all())

    today = now.astimezone(ZoneInfo(settings.timezone)).date()

    return ReminderPlanDTO(
        reminders=[
            ReminderItem(
                schedule_id=r.schedule_id,
                driver_id=driver_by_schedule[r.schedule_id],
                tg_user_id=r.tg_user_id,
                kind=r.kind,
                text=r.text,
            )
            for r in result.reminders
        ],
        owner_digest=result.owner_lines,
        today=today,
    )


@router.post("/mark", status_code=204, response_model=None)
async def mark(
    payload: ReminderMark,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> Response:
    on_date = payload.on_date or datetime.now(ZoneInfo(settings.timezone)).date()
    await reminders_domain.mark_reminded(session, payload.schedule_ids, on_date)
    return Response(status_code=204)
