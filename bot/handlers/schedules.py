"""Админ: настройка индивидуальных графиков платежей (FR-SCH-1..5)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import ScheduleCB
from bot.db.models import Driver, SchedulePeriod
from bot.filters import IsAdmin
from bot.keyboards.admin import BTN_SCHEDULES, admin_menu
from bot.keyboards.schedules import drivers_list_kb, period_kb, start_date_kb
from bot.services import drivers as drivers_service
from bot.services import schedules as sched_service
from bot.states.schedule import SetSchedule

router = Router(name="schedules")
router.message.filter(IsAdmin)
router.callback_query.filter(IsAdmin)


def _today_utc() -> datetime:
    now = datetime.now(timezone.utc)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)


@router.message(F.text == BTN_SCHEDULES)
async def open_schedules(message: Message, session: AsyncSession) -> None:
    drivers = await drivers_service.list_drivers(session)
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
    session: AsyncSession,
    state: FSMContext,
) -> None:
    driver = await session.get(Driver, callback_data.driver_id)
    if driver is None:
        await query.answer("Водитель не найден.", show_alert=True)
        return

    current = await sched_service.get_schedule(session, driver.id)
    info = ""
    if current is not None:
        info = (
            "Текущий график: "
            f"{sched_service.period_label(current.period, current.interval_days)}, "
            f"{current.amount} · след. платёж "
            f"{current.next_due_date:%d.%m.%Y}\n\n"
        )
    await state.clear()
    await state.update_data(driver_id=driver.id)
    await query.message.answer(
        f"{info}Водитель: <b>{driver.full_name}</b>\nВыберите периодичность:",
        reply_markup=period_kb(driver.id),
    )
    await query.answer()


@router.callback_query(ScheduleCB.filter(F.action == "set_period"))
async def set_period(
    query: CallbackQuery, callback_data: ScheduleCB, state: FSMContext
) -> None:
    period = SchedulePeriod(callback_data.value)
    await state.update_data(period=period.value, driver_id=callback_data.driver_id)

    if period is SchedulePeriod.custom:
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
        amount = float(text)
    except ValueError:
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
async def start_today(
    query: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    await _finish(query.message, state, session, _today_utc())
    await query.answer()


@router.callback_query(ScheduleCB.filter(F.action == "start_tomorrow"))
async def start_tomorrow(
    query: CallbackQuery, state: FSMContext, session: AsyncSession
) -> None:
    await _finish(query.message, state, session, _today_utc() + timedelta(days=1))
    await query.answer()


@router.message(SetSchedule.start_date, F.text)
async def start_manual(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    try:
        d = datetime.strptime(message.text.strip(), "%d.%m.%Y")
    except ValueError:
        await message.answer("Неверный формат. Введите дату как ДД.ММ.ГГГГ.")
        return
    await _finish(message, state, session, d.replace(tzinfo=timezone.utc))


async def _finish(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    next_due: datetime,
) -> None:
    data = await state.get_data()
    period = SchedulePeriod(data["period"])
    schedule = await sched_service.set_schedule(
        session,
        driver_id=data["driver_id"],
        period=period,
        interval_days=data.get("interval_days"),
        amount=data["amount"],
        next_due_date=next_due,
    )
    await state.clear()
    await message.answer(
        "✅ График сохранён:\n"
        f"Периодичность: {sched_service.period_label(period, schedule.interval_days)}\n"
        f"Сумма: {schedule.amount}\n"
        f"Первый платёж: {schedule.next_due_date:%d.%m.%Y}",
        reply_markup=admin_menu(),
    )
