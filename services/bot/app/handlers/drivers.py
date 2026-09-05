"""Админ: раздел «Водители» — список, карточка, увольнение."""
from __future__ import annotations

import logging
from datetime import datetime
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.types import CallbackQuery, Message

from app.callbacks import DriverCB
from app.client import ApiClient, ApiError
from app.filters import IsAdmin
from app.keyboards.admin import BTN_DRIVERS
from app.keyboards.drivers import driver_card_kb, drivers_list_kb, fire_confirm_kb
from app.middlewares.role import RoleMiddleware

logger = logging.getLogger(__name__)
router = Router(name="drivers")
router.message.filter(IsAdmin)
router.callback_query.filter(IsAdmin)


async def _show_list(message: Message, api: ApiClient, *, fired: bool) -> None:
    drivers = await api.drivers(active=not fired)
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
async def open_drivers(message: Message, api: ApiClient) -> None:
    await _show_list(message, api, fired=False)


@router.callback_query(DriverCB.filter(F.action == "list_active"))
async def list_active(query: CallbackQuery, api: ApiClient) -> None:
    await _show_list(query.message, api, fired=False)
    await query.answer()


@router.callback_query(DriverCB.filter(F.action == "list_fired"))
async def list_fired(query: CallbackQuery, api: ApiClient) -> None:
    await _show_list(query.message, api, fired=True)
    await query.answer()


def _fmt_money(value: object) -> str:
    return f"{Decimal(str(value)):.2f}"


async def _card_text(api: ApiClient, driver: dict, stats: dict) -> str:
    lines = [
        f"👤 <b>{driver['full_name']}</b>",
        f"Телефон: {driver.get('phone') or '—'}",
        f"ИНН: {driver.get('inn') or '—'}",
        f"Машина: {driver.get('car_plate') or '—'}",
    ]

    # Расчёт просрочки — на стороне core-api (schedule_status), бот только рисует.
    try:
        schedule_info = await api.get_schedule(driver["id"])
    except ApiError:
        schedule_info = None
    status = schedule_info.get("status") if schedule_info else None
    if status is not None:
        lines.append(f"График: {status['period_label']}, {_fmt_money(status['amount'])}")
        lines.append(f"Состояние: {status['summary']}")
    else:
        lines.append("График: не назначен")

    lines.append(
        f"Оплачено всего: {_fmt_money(stats.get('total_paid', 0))} "
        f"({stats.get('payments_count', 0)} платеж.)"
    )
    if not driver.get("active", True):
        fired_raw = driver.get("fired_at")
        when = f" {datetime.fromisoformat(fired_raw):%d.%m.%Y}" if fired_raw else ""
        lines.append(f"\n🚫 <b>Уволен{when}</b>")
    return "\n".join(lines)


@router.callback_query(DriverCB.filter(F.action == "view"))
async def view_driver(
    query: CallbackQuery, callback_data: DriverCB, api: ApiClient
) -> None:
    try:
        info = await api.driver(callback_data.driver_id)
    except ApiError as exc:
        await query.answer(exc.human, show_alert=True)
        return

    driver = info["driver"]
    stats = info.get("stats") or {}
    text = await _card_text(api, driver, stats)
    if driver.get("selfie_file_id"):
        await query.message.answer_photo(
            driver["selfie_file_id"], caption=text, reply_markup=driver_card_kb(driver)
        )
    else:
        await query.message.answer(text, reply_markup=driver_card_kb(driver))
    await query.answer()


@router.callback_query(DriverCB.filter(F.action == "fire"))
async def fire_ask(
    query: CallbackQuery, callback_data: DriverCB, api: ApiClient
) -> None:
    try:
        info = await api.driver(callback_data.driver_id)
    except ApiError as exc:
        await query.answer(exc.human, show_alert=True)
        return
    driver = info["driver"]
    if not driver.get("active", True):
        await query.answer("Водитель уже уволен.", show_alert=True)
        return

    car = driver.get("car_plate") or "—"
    await query.message.answer(
        f"Уволить водителя <b>{driver['full_name']}</b>?\n\n"
        f"Машина {car} освободится, график остановится, доступ к боту закроется.\n"
        "История платежей сохранится — водитель уйдёт в архив.",
        reply_markup=fire_confirm_kb(driver),
    )
    await query.answer()


@router.callback_query(DriverCB.filter(F.action == "fire_confirm"))
async def fire_confirm(
    query: CallbackQuery,
    callback_data: DriverCB,
    api: ApiClient,
    bot: Bot,
    role_mw: RoleMiddleware,
) -> None:
    try:
        info = await api.driver(callback_data.driver_id)
    except ApiError as exc:
        await query.answer(exc.human, show_alert=True)
        return
    driver = info["driver"]
    if not driver.get("active", True):
        await query.answer("Водитель уже уволен.", show_alert=True)
        return

    name = driver["full_name"]
    tg_user_id = driver["tg_user_id"]

    try:
        result = await api.fire_driver(driver["id"], tg_id=query.from_user.id)
    except ApiError as exc:
        await query.answer(exc.human, show_alert=True)
        return
    plate = (result or {}).get("freed_plate")

    await query.message.answer(
        f"🚫 Водитель <b>{name}</b> уволен."
        + (f"\nМашина {plate} снова свободна." if plate else "")
    )
    await query.answer()

    # Роль уволенного изменилась — кэш RoleMiddleware иначе покажет старое меню.
    role_mw.invalidate(tg_user_id)

    # Предупреждаем водителя — иначе он просто обнаружит, что бот его не узнаёт.
    try:
        await bot.send_message(
            tg_user_id,
            "Вы откреплены от машины, доступ к боту закрыт. "
            "По вопросам обратитесь к владельцу автопарка.",
        )
    except Exception as e:  # noqa: BLE001
        logger.warning("Не удалось уведомить уволенного %s: %s", tg_user_id, e)
