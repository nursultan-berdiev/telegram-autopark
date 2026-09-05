"""Приём платежей водителем + распознавание чека через core-api (FR-PAY, FR-AI-1..5)."""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from decimal import Decimal

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Document, Message

from app.callbacks import PaymentCB
from app.client import ApiClient, ApiError
from app.config import settings
from app.filters import IsDriver
from app.keyboards.driver_menu import (
    BTN_MY_SCHEDULE,
    BTN_PAY,
    confirm_payment_kb,
    driver_menu,
)
from app.states.payment import PaymentFlow

logger = logging.getLogger(__name__)
router = Router(name="payments")
router.message.filter(IsDriver)
router.callback_query.filter(IsDriver)


def _fmt_money(value: object) -> str:
    return f"{Decimal(str(value)):.2f}"


@router.message(F.text == BTN_MY_SCHEDULE)
async def my_schedule(message: Message, api: ApiClient, driver: dict) -> None:
    resp = await api.get_schedule(driver["id"])
    schedule, status = resp.get("schedule"), resp.get("status")
    if schedule is None or status is None:
        await message.answer("График платежей ещё не назначен. Ожидайте владельца.")
        return

    lines = [
        "Ваш график платежей:",
        f"Периодичность: {status['period_label']}",
        f"Сумма за период: {_fmt_money(status['amount'])}",
    ]
    if Decimal(str(status["paid_in_period"])) > 0:
        lines.append(f"Внесено в текущий период: {_fmt_money(status['paid_in_period'])}")
    if status["is_overdue"]:
        head = (
            "Срок сегодня."
            if status["overdue_days"] < 1
            else f"⚠️ Просрочка {status['overdue_days']} дн."
        )
        lines.append(f"{head} К оплате: {_fmt_money(status['debt_now'])}")
    else:
        next_due = datetime.fromisoformat(status["next_due_date"])
        lines.append(f"Ближайший платёж: {next_due:%d.%m.%Y}")
        lines.append(
            f"Осталось внести к сроку: {_fmt_money(status['remaining_current'])}"
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
    api: ApiClient,
    driver: dict,
    bot: Bot,
) -> None:
    if message.photo:
        file_id = message.photo[-1].file_id
        receipt_kind, media_type, size = "photo", "image/jpeg", message.photo[-1].file_size
    else:
        document = message.document
        media_type = _document_media_type(document)
        if media_type is None:
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
    buf = await bot.download(file_id)
    image_bytes = buf.read()
    rhash = hashlib.sha256(image_bytes).hexdigest()

    try:
        ext = media_type.split("/")[-1]
        recognized = await api.recognize_receipt(
            image_bytes, media_type, filename=f"receipt.{ext}"
        )
    except ApiError as exc:
        logger.warning("Ошибка распознавания чека: %s", exc)
        await message.answer(
            "Не удалось распознать чек (ошибка сервиса). Попробуйте позже или "
            "отправьте фото повторно."
        )
        return

    if recognized.get("amount") is None:
        await message.answer(
            "Не удалось распознать данные чека. Убедитесь, что на чеке видны "
            "сумма и дата, и отправьте его ещё раз."
        )
        return

    # Храним весь ответ recognize_receipt как есть — то же самое уйдёт в
    # create_payment после подтверждения (recognized=...).
    await state.update_data(
        file_id=file_id,
        receipt_kind=receipt_kind,
        rhash=rhash,
        recognized=recognized,
    )
    await state.set_state(PaymentFlow.confirm)

    paid_at = recognized.get("paid_at")
    when = f"{datetime.fromisoformat(paid_at):%d.%m.%Y %H:%M}" if paid_at else "не указано"
    cur = f" {recognized['currency']}" if recognized.get("currency") else ""
    await message.answer(
        "Проверьте распознанные данные:\n"
        f"💰 Сумма: {recognized['amount']}{cur}\n"
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
    api: ApiClient,
    driver: dict,
    bot: Bot,
) -> None:
    data = await state.get_data()
    recognized = data["recognized"]

    try:
        result = await api.create_payment(
            driver_id=driver["id"],
            amount=recognized["amount"],
            paid_at=recognized.get("paid_at"),
            receipt_file_id=data["file_id"],
            receipt_kind=data.get("receipt_kind", "photo"),
            receipt_hash=data["rhash"],
            recognized=recognized,
        )
    except ApiError as exc:
        await state.clear()
        # Дубль чека — 409 от create_payment (дедуп теперь целиком на core-api).
        text = "Этот чек уже был принят ранее. Отправьте другой чек." if exc.status_code == 409 else exc.human
        await query.message.answer(text, reply_markup=driver_menu())
        await query.answer()
        return

    await state.clear()
    await query.message.answer(_driver_receipt_text(result), reply_markup=driver_menu())
    await query.answer()

    await _notify_owner(bot, driver, result)


def _driver_receipt_text(result: dict) -> str:
    """Сообщение водителю с учётом частичной/полной оплаты."""
    payment = result["payment"]
    head = f"✅ Платёж на сумму {_fmt_money(payment['amount'])} принят."
    next_due_raw = result.get("next_due_date")
    if next_due_raw is None:
        return head + " Спасибо!"  # графика ещё нет — просто фиксируем оплату

    next_due = datetime.fromisoformat(next_due_raw)
    periods_closed = result.get("periods_closed", 0)
    if periods_closed >= 1:
        text = (
            f"{head} Закрыто периодов: {periods_closed}. "
            f"Следующий платёж: {next_due:%d.%m.%Y}."
        )
        # Переплата переносится в следующий период — водитель должен это видеть.
        prepaid = Decimal(str(result.get("paid_in_period", 0) or 0))
        if prepaid > 0:
            text += f" Учтена предоплата {_fmt_money(prepaid)}."
        return text
    # Частичная оплата — период ещё не закрыт.
    return (
        f"{head} Зачтено как частичная оплата, остаток до закрытия периода: "
        f"{_fmt_money(result.get('remaining_current', 0))} (срок {next_due:%d.%m.%Y})."
    )


async def _notify_owner(bot: Bot, driver: dict, result: dict) -> None:
    payment = result["payment"]
    lines = [
        "💵 Новый платёж:",
        f"Водитель: {driver['full_name']}",
        f"Машина: {driver.get('car_plate') or '—'}",
        f"Сумма: {_fmt_money(payment['amount'])}",
    ]
    if payment.get("paid_at"):
        paid_at = datetime.fromisoformat(payment["paid_at"])
        lines.append(f"Дата платежа: {paid_at:%d.%m.%Y %H:%M}")

    next_due_raw = result.get("next_due_date")
    if next_due_raw is not None:
        next_due = datetime.fromisoformat(next_due_raw)
        periods_closed = result.get("periods_closed", 0)
        if periods_closed >= 1:
            lines.append(
                f"Закрыто периодов: {periods_closed}, след. срок {next_due:%d.%m.%Y}"
            )
        else:
            lines.append(
                f"⚠️ Частичная оплата: остаток по периоду "
                f"{_fmt_money(result.get('remaining_current', 0))} (срок {next_due:%d.%m.%Y})"
            )
    text = "\n".join(lines)

    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
            await _send_receipt(bot, admin_id, payment)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось уведомить админа %s: %s", admin_id, exc)


async def _send_receipt(bot: Bot, chat_id: int, payment: dict) -> None:
    """Пересылает чек владельцу тем же типом, каким он пришёл.

    file_id документа (скриншот-файл, PDF) через send_photo Telegram не примет.
    """
    if not payment.get("receipt_file_id"):
        return
    if payment.get("receipt_kind") == "document":
        await bot.send_document(chat_id, payment["receipt_file_id"], caption="Чек")
    else:
        await bot.send_photo(chat_id, payment["receipt_file_id"], caption="Чек")
