"""Админ: кнопочные отчёты (FR-RPT-1..3)."""
from __future__ import annotations

from datetime import datetime, timezone

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import ReportCB
from bot.db.models import CarStatus
from bot.filters import IsAdmin
from bot.keyboards.admin import BTN_REPORTS
from bot.keyboards.reports import reports_menu_kb
from bot.services import reports as reports_service

router = Router(name="reports")
router.message.filter(IsAdmin)
router.callback_query.filter(IsAdmin)

_MAX_LINES = 50  # защитный предел вывода на одно сообщение


@router.message(F.text == BTN_REPORTS)
async def open_reports(message: Message) -> None:
    await message.answer("Выберите отчёт:", reply_markup=reports_menu_kb())


@router.callback_query(ReportCB.filter(F.kind == "cars"))
async def report_cars(
    query: CallbackQuery, session: AsyncSession
) -> None:
    cars = await reports_service.cars_with_drivers(session)
    if not cars:
        await _reply(query, "В автопарке нет машин.")
        return
    lines = ["🚗 <b>Машины и водители</b>\n"]
    for car in cars:
        if car.status == CarStatus.occupied and car.driver:
            who = car.driver.full_name
            lines.append(f"🔴 {car.plate} — {who}")
        else:
            lines.append(f"🟢 {car.plate} — свободна")
    await _reply(query, "\n".join(lines))


@router.callback_query(ReportCB.filter(F.kind == "upcoming"))
async def report_upcoming(query: CallbackQuery, session: AsyncSession) -> None:
    items = await reports_service.upcoming_payments(session)
    if not items:
        await _reply(query, "Нет водителей с назначенным графиком.")
        return
    now = datetime.now(timezone.utc)
    lines = ["⏰ <b>Кому скоро платить</b>\n"]
    for it in items:
        due = it.next_due
        if getattr(due, "tzinfo", None) is None:
            due_cmp = due.replace(tzinfo=timezone.utc)
        else:
            due_cmp = due
        mark = "⚠️ просрочен" if due_cmp < now else ""
        lines.append(
            f"{it.name} · {it.car_plate}: {it.amount} — {due:%d.%m.%Y} {mark}".strip()
        )
    await _reply(query, "\n".join(lines))


@router.callback_query(ReportCB.filter(F.kind == "by_driver"))
async def report_by_driver(query: CallbackQuery, session: AsyncSession) -> None:
    rows = await reports_service.statement_by_driver(session)
    if not rows:
        await _reply(query, "Нет данных.")
        return
    lines = ["📄 <b>Выписка по водителям</b>\n"]
    for r in rows:
        lines.append(f"{r.name} · {r.car_plate}: {r.total} ({r.count} платеж.)")
    await _reply(query, "\n".join(lines))


@router.callback_query(ReportCB.filter(F.kind == "by_car"))
async def report_by_car(query: CallbackQuery, session: AsyncSession) -> None:
    rows = await reports_service.statement_by_car(session)
    if not rows:
        await _reply(query, "Нет машин.")
        return
    lines = ["📄 <b>Выписка по машинам</b>\n"]
    for r in rows:
        lines.append(f"{r.plate}: {r.total} ({r.count} платеж.)")
    await _reply(query, "\n".join(lines))


async def _reply(query: CallbackQuery, text: str) -> None:
    """Отправляет отчёт, разбивая длинный вывод на части."""
    body = text.split("\n")
    for i in range(0, len(body), _MAX_LINES):
        await query.message.answer("\n".join(body[i : i + _MAX_LINES]))
    await query.answer()
