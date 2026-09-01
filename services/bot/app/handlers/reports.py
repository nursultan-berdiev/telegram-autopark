"""Админ: кнопочные отчёты (FR-RPT-1..3)."""
from __future__ import annotations

from decimal import Decimal

from aiogram import F, Router
from aiogram.types import CallbackQuery, Message

from app.callbacks import ReportCB
from app.client import ApiClient
from app.filters import IsAdmin
from app.keyboards.admin import BTN_REPORTS
from app.keyboards.reports import reports_menu_kb

router = Router(name="reports")
router.message.filter(IsAdmin)
router.callback_query.filter(IsAdmin)

_MAX_LINES = 50  # защитный предел вывода на одно сообщение


def _fmt_money(value: object) -> str:
    return f"{Decimal(str(value)):.2f}"


@router.message(F.text == BTN_REPORTS)
async def open_reports(message: Message) -> None:
    await message.answer("Выберите отчёт:", reply_markup=reports_menu_kb())


@router.callback_query(ReportCB.filter(F.kind == "cars"))
async def report_cars(query: CallbackQuery, api: ApiClient) -> None:
    cars = await api.report_cars_drivers()
    if not cars:
        await _reply(query, "В автопарке нет машин.")
        return
    lines = ["🚗 <b>Машины и водители</b>\n"]
    for car in cars:
        if car["status"] == "occupied" and car.get("driver_name"):
            lines.append(f"🔴 {car['plate']} — {car['driver_name']}")
        else:
            lines.append(f"🟢 {car['plate']} — свободна")
    await _reply(query, "\n".join(lines))


def _upcoming_text(items: list[dict]) -> str:
    """В день срока просрочки ещё нет — считаем её со следующего дня."""
    overdue_items = [it for it in items if it.get("overdue_days", 0) >= 1]
    total_debt = sum(Decimal(str(it["debt_now"])) for it in overdue_items)
    header = "⏰ <b>Кому скоро платить</b>"
    if overdue_items:
        header += (
            f"\nВ просрочке: {len(overdue_items)}, "
            f"суммарный долг: {_fmt_money(total_debt)}"
        )
    lines = [header + "\n"]
    for it in items:
        mark = "⚠️ " if it.get("overdue_days", 0) >= 1 else ""
        plate = it.get("plate") or "—"
        lines.append(f"{mark}{it['driver_name']} · {plate}: {it['summary']}")
    return "\n".join(lines)


@router.callback_query(ReportCB.filter(F.kind == "upcoming"))
async def report_upcoming(query: CallbackQuery, api: ApiClient) -> None:
    items = await api.report_upcoming()
    if not items:
        await _reply(query, "Нет водителей с назначенным графиком.")
        return
    await _reply(query, _upcoming_text(items))


@router.callback_query(ReportCB.filter(F.kind == "by_driver"))
async def report_by_driver(query: CallbackQuery, api: ApiClient) -> None:
    rows = await api.report_by_driver()
    if not rows:
        await _reply(query, "Нет данных.")
        return
    lines = ["📄 <b>Выписка по водителям</b>\n"]
    for r in rows:
        who = r["driver_name"]
        if r.get("car_plate"):
            who += f" ({r['car_plate']})"
        lines.append(
            f"{who}: {_fmt_money(r['total'])} ({r['payments_count']} платеж.)"
        )
    await _reply(query, "\n".join(lines))


@router.callback_query(ReportCB.filter(F.kind == "by_car"))
async def report_by_car(query: CallbackQuery, api: ApiClient) -> None:
    rows = await api.report_by_car()
    if not rows:
        await _reply(query, "Нет машин.")
        return
    lines = ["📄 <b>Выписка по машинам</b>\n"]
    for r in rows:
        lines.append(f"{r['plate']}: {_fmt_money(r['total'])} ({r['payments_count']} платеж.)")
    await _reply(query, "\n".join(lines))


async def _reply(query: CallbackQuery, text: str) -> None:
    """Отправляет отчёт, разбивая длинный вывод на части."""
    body = text.split("\n")
    for i in range(0, len(body), _MAX_LINES):
        await query.message.answer("\n".join(body[i : i + _MAX_LINES]))
    await query.answer()
