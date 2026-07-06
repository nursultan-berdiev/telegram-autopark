"""Приём платежей водителем + ИИ-распознавание чека (FR-PAY, FR-AI-1..5)."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import PaymentCB
from bot.config import settings
from bot.db.models import Car, Driver
from bot.filters import IsDriver
from bot.keyboards.driver_menu import (
    BTN_MY_SCHEDULE,
    BTN_PAY,
    confirm_payment_kb,
    driver_menu,
)
from bot.services import ai as ai_service
from bot.services import payments as payments_service
from bot.services import schedules as sched_service
from bot.services.storage import download_file_bytes, save_telegram_file
from bot.states.payment import PaymentFlow

logger = logging.getLogger(__name__)
router = Router(name="payments")
router.message.filter(IsDriver)
router.callback_query.filter(IsDriver)


@router.message(F.text == BTN_MY_SCHEDULE)
async def my_schedule(message: Message, session: AsyncSession, driver: Driver) -> None:
    schedule = await sched_service.get_schedule(session, driver.id)
    if schedule is None:
        await message.answer("График платежей ещё не назначен. Ожидайте владельца.")
        return
    await message.answer(
        "Ваш график платежей:\n"
        f"Периодичность: "
        f"{sched_service.period_label(schedule.period, schedule.interval_days)}\n"
        f"Сумма: {schedule.amount}\n"
        f"Ближайший платёж: {schedule.next_due_date:%d.%m.%Y}"
    )


@router.message(F.text == BTN_PAY)
async def pay_start(message: Message, state: FSMContext) -> None:
    await state.set_state(PaymentFlow.waiting_receipt)
    await message.answer(
        "Отправьте фотографию чека об оплате. Для отмены — /cancel."
    )


@router.message(PaymentFlow.waiting_receipt, F.photo)
async def pay_receipt(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    driver: Driver,
    bot: Bot,
) -> None:
    file_id = message.photo[-1].file_id

    await message.answer("⏳ Распознаю чек, подождите...")
    image_bytes, media_type = await download_file_bytes(bot, file_id)

    # Защита от повторной отправки одного и того же чека.
    rhash = payments_service.receipt_hash(image_bytes)
    if await payments_service.is_duplicate(session, rhash):
        await state.clear()
        await message.answer(
            "Этот чек уже был принят ранее. Отправьте другой чек.",
            reply_markup=driver_menu(),
        )
        return

    try:
        recognized = await ai_service.recognize_receipt(image_bytes, media_type)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка распознавания чека: %s", exc)
        await message.answer(
            "Не удалось распознать чек (ошибка сервиса). Попробуйте позже или "
            "отправьте фото повторно."
        )
        return

    if not recognized.readable or recognized.amount is None:
        await message.answer(
            "Не удалось распознать данные чека. Убедитесь, что на фото виден "
            "платёжный чек, и отправьте его ещё раз."
        )
        return

    # Сохраняем файл на сервере и данные — в FSM до подтверждения.
    receipt_path = await save_telegram_file(
        bot, file_id, subdir="receipts", name=rhash[:16]
    )
    await state.update_data(
        file_id=file_id,
        receipt_path=receipt_path,
        rhash=rhash,
        amount=recognized.amount,
        currency=recognized.currency,
        paid_at_raw=recognized.paid_at_raw,
        paid_at_iso=recognized.paid_at.isoformat() if recognized.paid_at else None,
    )
    await state.set_state(PaymentFlow.confirm)

    when = recognized.paid_at_raw or "не указано"
    cur = f" {recognized.currency}" if recognized.currency else ""
    await message.answer(
        "Проверьте распознанные данные:\n"
        f"💰 Сумма: {recognized.amount}{cur}\n"
        f"🕒 Дата/время: {when}\n\n"
        "Всё верно?",
        reply_markup=confirm_payment_kb(),
    )


@router.message(PaymentFlow.waiting_receipt)
async def pay_receipt_invalid(message: Message) -> None:
    await message.answer("Нужна фотография чека. Пришлите фото или /cancel.")


@router.callback_query(PaymentCB.filter(F.action == "retry"), PaymentFlow.confirm)
async def pay_retry(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PaymentFlow.waiting_receipt)
    await query.message.answer("Отправьте фотографию чека заново.")
    await query.answer()


@router.callback_query(PaymentCB.filter(F.action == "confirm"), PaymentFlow.confirm)
async def pay_confirm(
    query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    driver: Driver,
    bot: Bot,
) -> None:
    from datetime import datetime

    data = await state.get_data()
    rhash = data["rhash"]

    # Повторная проверка дубликата на момент подтверждения.
    if await payments_service.is_duplicate(session, rhash):
        await state.clear()
        await query.message.answer("Этот чек уже был принят.", reply_markup=driver_menu())
        await query.answer()
        return

    paid_at = (
        datetime.fromisoformat(data["paid_at_iso"]) if data.get("paid_at_iso") else None
    )
    recognized = ai_service.RecognizedReceipt(
        readable=True,
        amount=data["amount"],
        currency=data.get("currency"),
        paid_at=paid_at,
        paid_at_raw=data.get("paid_at_raw"),
        note=None,
    )

    payment = await payments_service.create_payment(
        session,
        driver_id=driver.id,
        car_id=driver.car_id,
        amount=data["amount"],
        paid_at=paid_at,
        receipt_file_id=data["file_id"],
        receipt_path=data["receipt_path"],
        receipt_hash=rhash,
        recognized=recognized,
    )

    # Сдвигаем график на следующий период, если он есть.
    schedule = await sched_service.get_schedule(session, driver.id)
    if schedule is not None:
        await sched_service.bump_after_payment(session, schedule)

    # Гос. номер машины загружаем явно (ленивую связь в async трогать нельзя).
    car = await session.get(Car, driver.car_id) if driver.car_id else None
    car_plate = car.plate if car else "—"

    await state.clear()
    await query.message.answer(
        f"✅ Платёж на сумму {payment.amount} принят. Спасибо!",
        reply_markup=driver_menu(),
    )
    await query.answer()

    await _notify_owner(bot, driver, car_plate, payment)


async def _notify_owner(bot: Bot, driver: Driver, car_plate: str, payment) -> None:
    lines = [
        "💵 Новый платёж:",
        f"Водитель: {driver.full_name}",
        f"Машина: {car_plate}",
        f"Сумма: {payment.amount}",
    ]
    if payment.paid_at:
        lines.append(f"Дата платежа: {payment.paid_at:%d.%m.%Y %H:%M}")
    text = "\n".join(lines)

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
            if payment.receipt_file_id:
                await bot.send_photo(admin_id, payment.receipt_file_id, caption="Чек")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось уведомить админа %s: %s", admin_id, exc)
