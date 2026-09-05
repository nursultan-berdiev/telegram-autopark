"""Админ: раздел «Водители» — список, карточка, увольнение."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import DriverCB
from bot.db.models import Driver
from bot.filters import IsAdmin
from bot.keyboards.admin import BTN_DRIVERS
from bot.keyboards.drivers import driver_card_kb, drivers_list_kb, fire_confirm_kb
from bot.services import drivers as drivers_service
from bot.services import schedules as sched_service

logger = logging.getLogger(__name__)
router = Router(name="drivers")
router.message.filter(IsAdmin)
router.callback_query.filter(IsAdmin)


async def _show_list(message: Message, session: AsyncSession, *, fired: bool) -> None:
    drivers = await drivers_service.list_drivers(session, active=not fired)
    if not drivers:
        text = (
            "Уволенных водителей нет."
            if fired
            else "Пока нет водителей. Выдайте приглашение кнопкой «➕ Новый водитель»."
        )
        await message.answer(text)
        return
    title = "🗂 <b>Уволенные</b>" if fired else "👤 <b>Водители</b>"
    await message.answer(
        f"{title} — {len(drivers)}:",
        reply_markup=drivers_list_kb(drivers, fired=fired),
    )


@router.message(F.text == BTN_DRIVERS)
async def open_drivers(message: Message, session: AsyncSession) -> None:
    await _show_list(message, session, fired=False)


@router.callback_query(DriverCB.filter(F.action == "list_active"))
async def list_active(query: CallbackQuery, session: AsyncSession) -> None:
    await _show_list(query.message, session, fired=False)
    await query.answer()


@router.callback_query(DriverCB.filter(F.action == "list_fired"))
async def list_fired(query: CallbackQuery, session: AsyncSession) -> None:
    await _show_list(query.message, session, fired=True)
    await query.answer()


async def _card_text(session: AsyncSession, driver: Driver) -> str:
    stats = await drivers_service.driver_stats(session, driver.id)
    schedule = await sched_service.get_schedule(session, driver.id)

    lines = [
        f"👤 <b>{driver.full_name}</b>",
        f"Телефон: {driver.phone}",
        f"ИНН: {driver.inn}",
        f"Машина: {driver.car.plate if driver.car else '—'}",
    ]
    if schedule is not None and schedule.active:
        st = sched_service.schedule_status(schedule)
        lines.append(
            f"График: {sched_service.period_label(schedule.period, schedule.interval_days)}, "
            f"{sched_service.fmt_money(st.amount)}"
        )
        lines.append(f"Состояние: {sched_service.due_summary(st)}")
    else:
        lines.append("График: не назначен")

    lines.append(
        f"Оплачено всего: {sched_service.fmt_money(stats.total_paid)} "
        f"({stats.payments_count} платеж.)"
    )
    if not driver.active:
        when = f" {driver.fired_at:%d.%m.%Y}" if driver.fired_at else ""
        lines.append(f"\n🚫 <b>Уволен{when}</b>")
    return "\n".join(lines)


@router.callback_query(DriverCB.filter(F.action == "view"))
async def view_driver(
    query: CallbackQuery, callback_data: DriverCB, session: AsyncSession
) -> None:
    driver = await drivers_service.get_driver(session, callback_data.driver_id)
    if driver is None:
        await query.answer("Водитель не найден.", show_alert=True)
        return

    text = await _card_text(session, driver)
    if driver.selfie_file_id:
        await query.message.answer_photo(
            driver.selfie_file_id, caption=text, reply_markup=driver_card_kb(driver)
        )
    else:
        await query.message.answer(text, reply_markup=driver_card_kb(driver))
    await query.answer()


@router.callback_query(DriverCB.filter(F.action == "fire"))
async def fire_ask(
    query: CallbackQuery, callback_data: DriverCB, session: AsyncSession
) -> None:
    driver = await drivers_service.get_driver(session, callback_data.driver_id)
    if driver is None or not driver.active:
        await query.answer("Водитель уже уволен или не найден.", show_alert=True)
        return

    car = driver.car.plate if driver.car else "—"
    await query.message.answer(
        f"Уволить водителя <b>{driver.full_name}</b>?\n\n"
        f"Машина {car} освободится, график остановится, доступ к боту закроется.\n"
        "История платежей сохранится — водитель уйдёт в архив.",
        reply_markup=fire_confirm_kb(driver),
    )
    await query.answer()


@router.callback_query(DriverCB.filter(F.action == "fire_confirm"))
async def fire_confirm(
    query: CallbackQuery, callback_data: DriverCB, session: AsyncSession, bot: Bot
) -> None:
    driver = await drivers_service.get_driver(session, callback_data.driver_id)
    if driver is None or not driver.active:
        await query.answer("Водитель уже уволен.", show_alert=True)
        return

    name = driver.full_name
    tg_user_id = driver.tg_user_id
    plate = await drivers_service.fire_driver(session, driver)

    await query.message.answer(
        f"🚫 Водитель <b>{name}</b> уволен."
        + (f"\nМашина {plate} снова свободна." if plate else "")
    )
    await query.answer()

    # Предупреждаем водителя — иначе он просто обнаружит, что бот его не узнаёт.
    try:
        await bot.send_message(
            tg_user_id,
            "Вы откреплены от машины, доступ к боту закрыт. "
            "По вопросам обратитесь к владельцу автопарка.",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Не удалось уведомить уволенного %s: %s", tg_user_id, e)
