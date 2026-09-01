"""Админ: настройка индивидуальных графиков платежей (FR-SCH-1..5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

from aiogram import Bot, F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.callbacks import ScheduleCB
from app.client import ApiClient, ApiError
from app.filters import IsAdmin
from app.keyboards.admin import BTN_SCHEDULES, admin_menu
from app.keyboards.schedules import drivers_list_kb, period_kb, start_date_kb
from app.scheduler import send_daily_reminders
from app.states.schedule import SetSchedule

router = Router(name="schedules")
router.message.filter(IsAdmin)
router.callback_query.filter(IsAdmin)


@router.message(Command("remind_now"))
async def remind_now(message: Message, bot: Bot, api: ApiClient) -> None:
    """Разовая рассылка напоминаний прямо сейчас (не дожидаясь REMINDER_HOUR).

    `/remind_now force` обходит антиспам «раз в день» — нужен для проверки
    на стенде, когда за сегодня напоминание уже уходило.
    """
    force = "force" in (message.text or "").lower()
    result = await send_daily_reminders(bot, api, force=force)
    await message.answer(
        "Рассылка выполнена.\n"
        f"Напоминаний водителям: {result['drivers']}\n"
        f"Сводок владельцу: {result['owners']}\n\n"
        "Если ноль — значит сегодня напоминать некому "
        "(нет активных графиков со сроком сегодня/завтра или просрочкой), "
        "либо водителям уже писали сегодня."
    )


def _today_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


def _fmt_money(value: object) -> str:
    return f"{Decimal(str(value)):.2f}"


@router.message(F.text == BTN_SCHEDULES)
async def open_schedules(message: Message, api: ApiClient) -> None:
    drivers = await api.drivers(active=True)
    if not drivers:
        await message.answer("Пока нет зарегистрированных водителей.")
        return
    await message.answer(
        "Выберите водителя для настройки графика платежей:",
        reply_markup=drivers_list_kb(drivers),
    )


@router.callback_query(ScheduleCB.filter(F.action == "pick_driver"))
async def pick_driver(
    query: CallbackQuery,
    callback_data: ScheduleCB,
    api: ApiClient,
    state: FSMContext,
) -> None:
    try:
        info = await api.driver(callback_data.driver_id)
    except ApiError:
        await query.answer("Водитель не найден.", show_alert=True)
        return
    driver = info["driver"]

    sched_resp = await api.get_schedule(driver["id"])
    schedule, status = sched_resp.get("schedule"), sched_resp.get("status")
    info_line = ""
    if schedule is not None and status is not None:
        next_due = datetime.fromisoformat(status["next_due_date"])
        info_line = (
            "Текущий график: "
            f"{status['period_label']}, "
            f"{_fmt_money(schedule['amount'])} · след. платёж "
            f"{next_due:%d.%m.%Y}\n\n"
        )
    await state.clear()
    await state.update_data(driver_id=driver["id"])
    await query.message.answer(
        f"{info_line}Водитель: <b>{driver['full_name']}</b>\nВыберите периодичность:",
        reply_markup=period_kb(driver["id"]),
    )
    await query.answer()


@router.callback_query(ScheduleCB.filter(F.action == "set_period"))
async def set_period(
    query: CallbackQuery, callback_data: ScheduleCB, state: FSMContext
) -> None:
    period = callback_data.value
    await state.update_data(period=period, driver_id=callback_data.driver_id)

    if period == "custom":
        await state.set_state(SetSchedule.interval_days)
        await query.message.answer("Введите интервал в днях (например, 10).")
    else:
        await state.set_state(SetSchedule.amount)
        await query.message.answer("Введите сумму платежа (например, 1500).")
    await query.answer()


@router.message(SetSchedule.interval_days, F.text)
async def set_interval(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if not text.isdigit() or int(text) < 1:
        await message.answer("Введите целое число дней (≥ 1).")
        return
    await state.update_data(interval_days=int(text))
    await state.set_state(SetSchedule.amount)
    await message.answer("Введите сумму платежа (например, 1500).")


@router.message(SetSchedule.amount, F.text)
async def set_amount(message: Message, state: FSMContext) -> None:
    text = message.text.strip().replace(",", ".")
    try:
        amount = Decimal(text)
    except (InvalidOperation, ValueError):
        await message.answer("Введите сумму числом (например, 1500).")
        return
    if amount <= 0:
        await message.answer("Сумма должна быть больше нуля.")
        return
    data = await state.get_data()
    await state.update_data(amount=amount)
    await state.set_state(SetSchedule.start_date)
    await message.answer(
        "Дата первого платежа? Выберите или введите вручную в формате ДД.ММ.ГГГГ.",
        reply_markup=start_date_kb(data["driver_id"]),
    )


@router.callback_query(ScheduleCB.filter(F.action == "start_today"))
async def start_today(query: CallbackQuery, state: FSMContext, api: ApiClient) -> None:
    await _finish(query.message, state, api, _today_utc())
    await query.answer()


@router.callback_query(ScheduleCB.filter(F.action == "start_tomorrow"))
async def start_tomorrow(
    query: CallbackQuery, state: FSMContext, api: ApiClient
) -> None:
    await _finish(query.message, state, api, _today_utc() + timedelta(days=1))
    await query.answer()


@router.message(SetSchedule.start_date, F.text)
async def start_manual(message: Message, state: FSMContext, api: ApiClient) -> None:
    try:
        d = datetime.strptime(message.text.strip(), "%d.%m.%Y")
    except (InvalidOperation, ValueError):
        await message.answer("Неверный формат. Введите дату как ДД.ММ.ГГГГ.")
        return
    await _finish(message, state, api, d.replace(tzinfo=timezone.utc))


async def _finish(
    message: Message,
    state: FSMContext,
    api: ApiClient,
    next_due: datetime,
) -> None:
    data = await state.get_data()
    try:
        resp = await api.set_schedule(
            data["driver_id"],
            period=data["period"],
            amount=data["amount"],
            next_due_date=next_due,
            interval_days=data.get("interval_days"),
        )
    except ApiError as exc:
        await state.clear()
        await message.answer(exc.human, reply_markup=admin_menu())
        return

    await state.clear()
    status = resp["status"]
    await message.answer(
        "✅ График сохранён:\n"
        f"Периодичность: {status['period_label']}\n"
        f"Сумма: {_fmt_money(status['amount'])}\n"
        f"{_due_line(status)}",
        reply_markup=admin_menu(),
    )


def _due_line(status: dict) -> str:
    """Состояние платежа сразу после сохранения графика.

    Дату первого платежа задают и задним числом — так заводят водителя, который
    уже должен. Долг/просрочку теперь считает core-api, тут только рендер.
    """
    next_due = datetime.fromisoformat(status["next_due_date"])
    if not status["is_overdue"]:
        return f"Следующий платёж: {next_due:%d.%m.%Y}"
    if status["overdue_days"] < 1:
        return (
            f"Первый платёж: {next_due:%d.%m.%Y}\n"
            f"📅 Срок сегодня. К оплате: {_fmt_money(status['debt_now'])}"
        )
    return (
        f"Первый платёж: {next_due:%d.%m.%Y}\n"
        f"⚠️ Платёж просрочен на {status['overdue_days']} дн. "
        f"К оплате: {_fmt_money(status['debt_now'])}"
    )
