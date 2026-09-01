"""Приём платежей водителем + ИИ-распознавание чека (FR-PAY, FR-AI-1..5)."""
from __future__ import annotations

import logging
from datetime import datetime

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Document, Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import PaymentCB
from bot.config import settings
from bot.db.models import Car, Driver, Payment
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
from bot.services.schedules import ApplyResult
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

    st = sched_service.schedule_status(schedule)
    lines = [
        "Ваш график платежей:",
        f"Периодичность: "
        f"{sched_service.period_label(schedule.period, schedule.interval_days)}",
        f"Сумма за период: {sched_service.fmt_money(st.amount)}",
    ]
    if st.paid_in_period > 0:
        lines.append(f"Внесено в текущий период: {sched_service.fmt_money(st.paid_in_period)}")
    if st.is_overdue:
        head = (
            "Срок сегодня."
            if st.overdue_days < 1
            else f"⚠️ Просрочка {st.overdue_days} дн."
        )
        lines.append(f"{head} К оплате: {sched_service.fmt_money(st.debt_now)}")
    else:
        lines.append(f"Ближайший платёж: {st.next_due_date:%d.%m.%Y}")
        lines.append(
            f"Осталось внести к сроку: {sched_service.fmt_money(st.remaining_current)}"
        )
    await message.answer("\n".join(lines))


# Чек приходит тремя способами: фотографией, скриншотом-файлом и PDF из
# банковского приложения. Принимаем все три — иначе водителю приходится
# пересохранять выписку в картинку.
RECEIPT_MEDIA_TYPES = {
    "image/jpeg",
    "image/jpg",
    "image/png",
    "image/webp",
    "application/pdf",
}
MAX_RECEIPT_BYTES = 15 * 1024 * 1024


def _document_media_type(document: Document) -> str | None:
    """Тип документа-чека или None, если такой файл чеком быть не может."""
    mime = (document.mime_type or "").lower()
    if mime in RECEIPT_MEDIA_TYPES:
        return mime
    name = (document.file_name or "").lower()
    if name.endswith(".pdf"):
        return "application/pdf"
    return None


@router.message(F.text == BTN_PAY)
async def pay_start(message: Message, state: FSMContext) -> None:
    await state.set_state(PaymentFlow.waiting_receipt)
    await message.answer(
        "Отправьте чек об оплате — фотографию, скриншот или PDF-файл. "
        "Для отмены — /cancel."
    )


@router.message(PaymentFlow.waiting_receipt, F.photo | F.document)
async def pay_receipt(
    message: Message,
    state: FSMContext,
    session: AsyncSession,
    driver: Driver,
    bot: Bot,
) -> None:
    if message.photo:
        file_id = message.photo[-1].file_id
        receipt_kind, doc_media_type, size = "photo", None, message.photo[-1].file_size
    else:
        document = message.document
        doc_media_type = _document_media_type(document)
        if doc_media_type is None:
            await message.answer(
                "Такой файл не подходит. Пришлите чек фотографией, скриншотом "
                "(JPG/PNG) или PDF-файлом."
            )
            return
        file_id = document.file_id
        receipt_kind, size = "document", document.file_size

    if size and size > MAX_RECEIPT_BYTES:
        await message.answer(
            f"Файл слишком большой ({size // (1024 * 1024)} МБ). "
            f"Максимум — {MAX_RECEIPT_BYTES // (1024 * 1024)} МБ."
        )
        return

    await message.answer("⏳ Распознаю чек, подождите...")
    image_bytes, media_type = await download_file_bytes(
        bot, file_id, media_type=doc_media_type
    )

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
            "Не удалось распознать данные чека. Убедитесь, что на чеке видны "
            "сумма и дата, и отправьте его ещё раз."
        )
        return

    # Сохраняем файл на сервере и данные — в FSM до подтверждения.
    receipt_path = await save_telegram_file(
        bot, file_id, subdir="receipts", name=rhash[:16]
    )
    await state.update_data(
        file_id=file_id,
        receipt_kind=receipt_kind,
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
    await message.answer(
        "Нужен сам чек. Пришлите фото, скриншот или PDF-файл — либо /cancel."
    )


@router.callback_query(PaymentCB.filter(F.action == "retry"), PaymentFlow.confirm)
async def pay_retry(query: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(PaymentFlow.waiting_receipt)
    await query.message.answer("Отправьте чек заново — фото, скриншот или PDF.")
    await query.answer()


@router.callback_query(PaymentCB.filter(F.action == "confirm"), PaymentFlow.confirm)
async def pay_confirm(
    query: CallbackQuery,
    state: FSMContext,
    session: AsyncSession,
    driver: Driver,
    bot: Bot,
) -> None:
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
        receipt_kind=data.get("receipt_kind", "photo"),
        commit=False,
    )

    # Засчитываем оплату в график: частичная копится, полная — сдвигает срок.
    schedule = await sched_service.get_schedule(session, driver.id)
    result = None
    if schedule is not None:
        result = await sched_service.apply_payment(
            session, schedule, data["amount"], commit=False
        )

    # Деньги и зачёт в график — одной транзакцией: иначе платёж запишется,
    # а график останется в долгу.
    await session.commit()

    # Гос. номер машины загружаем явно (ленивую связь в async трогать нельзя).
    car = await session.get(Car, driver.car_id) if driver.car_id else None
    car_plate = car.plate if car else "—"

    await state.clear()
    await query.message.answer(
        _driver_receipt_text(payment, result),
        reply_markup=driver_menu(),
    )
    await query.answer()

    await _notify_owner(bot, driver, car_plate, payment, result)


def _driver_receipt_text(payment: Payment, result: ApplyResult | None) -> str:
    """Сообщение водителю с учётом частичной/полной оплаты."""
    fmt = sched_service.fmt_money
    head = f"✅ Платёж на сумму {fmt(payment.amount)} принят."
    if result is None:
        return head + " Спасибо!"
    if result.periods_closed >= 1:
        tail = f"Следующий платёж: {result.next_due_date:%d.%m.%Y}."
        if result.paid_in_period > 0:
            tail += f" Учтена предоплата {fmt(result.paid_in_period)}."
        return f"{head} Закрыто периодов: {result.periods_closed}. {tail}"
    # Частичная оплата — период ещё не закрыт.
    return (
        f"{head} Зачтено как частичная оплата, остаток до закрытия периода: "
        f"{fmt(result.remaining_current)} (срок {result.next_due_date:%d.%m.%Y})."
    )


async def _notify_owner(
    bot: Bot,
    driver: Driver,
    car_plate: str,
    payment: Payment,
    result: ApplyResult | None,
) -> None:
    fmt = sched_service.fmt_money
    lines = [
        "💵 Новый платёж:",
        f"Водитель: {driver.full_name}",
        f"Машина: {car_plate}",
        f"Сумма: {fmt(payment.amount)}",
    ]
    if payment.paid_at:
        lines.append(f"Дата платежа: {payment.paid_at:%d.%m.%Y %H:%M}")
    if result is not None:
        if result.periods_closed >= 1:
            lines.append(
                f"Закрыто периодов: {result.periods_closed}, "
                f"след. срок {result.next_due_date:%d.%m.%Y}"
            )
        else:
            lines.append(
                f"⚠️ Частичная оплата: остаток по периоду "
                f"{fmt(result.remaining_current)} (срок {result.next_due_date:%d.%m.%Y})"
            )
    text = "\n".join(lines)

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
            await _send_receipt(bot, admin_id, payment)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось уведомить админа %s: %s", admin_id, exc)


async def _send_receipt(bot: Bot, chat_id: int, payment: Payment) -> None:
    """Пересылает чек владельцу тем же типом, каким он пришёл.

    file_id документа (скриншот-файл, PDF) через send_photo Telegram не примет.
    """
    if not payment.receipt_file_id:
        return
    if payment.receipt_kind == "document":
        await bot.send_document(chat_id, payment.receipt_file_id, caption="Чек")
    else:
        await bot.send_photo(chat_id, payment.receipt_file_id, caption="Чек")
