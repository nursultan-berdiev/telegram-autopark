"""Регистрация водителя по одноразовой ссылке-приглашению (FR-INV-4, FR-REG-1..4)."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Driver
from bot.keyboards.admin import admin_menu
from bot.keyboards.drivers import share_phone_kb
from bot.middlewares.role import Role
from bot.services import cars as cars_service
from bot.services import drivers as drivers_service
from bot.services import invitations as invitations_service
from bot.services.invitations import InviteProblem
from bot.services.storage import save_telegram_file
from bot.states.registration import Registration

logger = logging.getLogger(__name__)
router = Router(name="registration")

_ASK_OWNER = "Обратитесь к владельцу автопарка за новой ссылкой."

# Отказ объясняем по существу: «ссылка протухла» и «машину занял другой» —
# разные ситуации, и водителю от них нужно разное.
INVITE_PROBLEM_TEXT = {
    InviteProblem.not_found: f"Ссылка-приглашение недействительна. {_ASK_OWNER}",
    InviteProblem.expired: f"Срок действия ссылки-приглашения истёк. {_ASK_OWNER}",
    InviteProblem.used: (
        f"По этой ссылке уже зарегистрирован другой водитель. {_ASK_OWNER}"
    ),
    InviteProblem.car_taken: (
        f"Это транспортное средство уже занято другим водителем. {_ASK_OWNER}"
    ),
}


async def _already_registered_text(session: AsyncSession, driver: Driver | None) -> str:
    """Водителю важно понимать ПОЧЕМУ нельзя: он уже за машиной, а не «просто зарегистрирован»."""
    car = (
        await cars_service.get_car(session, driver.car_id)
        if driver is not None and driver.car_id
        else None
    )
    plate = f" <b>{car.plate}</b>" if car else ""
    return (
        f"Вы уже закреплены за транспортным средством{plate}. "
        "Повторная регистрация не нужна."
    )


@router.message(CommandStart(deep_link=True))
async def start_by_invite(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    session: AsyncSession,
    role: Role,
    driver: Driver | None = None,
) -> None:
    if role is Role.admin:
        await message.answer("Вы администратор.", reply_markup=admin_menu())
        return
    if role is Role.driver:
        await message.answer(await _already_registered_text(session, driver))
        return

    code = command.args or ""
    check = await invitations_service.resolve_invitation(session, code)
    if not check.ok:
        await message.answer(INVITE_PROBLEM_TEXT[check.problem])
        return

    invitation = check.invitation
    car = await cars_service.get_car(session, invitation.car_id)
    car_title = car.plate if car else "—"

    await state.clear()
    await state.update_data(invite_code=code, car_id=invitation.car_id)
    await state.set_state(Registration.full_name)
    await message.answer(
        f"Добро пожаловать! Регистрация водителя на машину <b>{car_title}</b>.\n\n"
        "Шаг 1 из 4. Введите ФИО полностью (например, Иванов Иван Иванович)."
    )


@router.message(Registration.full_name, F.text)
async def reg_full_name(message: Message, state: FSMContext) -> None:
    full_name = message.text.strip()
    if len(full_name) < 3:
        await message.answer("Слишком коротко. Введите ФИО полностью.")
        return
    await state.update_data(full_name=full_name)
    await state.set_state(Registration.phone)
    await message.answer(
        "Шаг 2 из 4. Отправьте номер телефона кнопкой ниже или введите вручную.",
        reply_markup=share_phone_kb(),
    )


@router.message(Registration.phone, F.contact)
async def reg_phone_contact(message: Message, state: FSMContext) -> None:
    await _save_phone(message, state, message.contact.phone_number)


@router.message(Registration.phone, F.text)
async def reg_phone_text(message: Message, state: FSMContext) -> None:
    phone = message.text.strip()
    digits = phone.lstrip("+").replace(" ", "").replace("-", "")
    if not digits.isdigit() or not (9 <= len(digits) <= 15):
        await message.answer("Не похоже на номер телефона. Попробуйте ещё раз.")
        return
    await _save_phone(message, state, phone)


async def _save_phone(message: Message, state: FSMContext, phone: str) -> None:
    await state.update_data(phone=phone)
    await state.set_state(Registration.inn)
    await message.answer(
        "Шаг 3 из 4. Введите ваш ИНН.", reply_markup=ReplyKeyboardRemove()
    )


@router.message(Registration.inn, F.text)
async def reg_inn(message: Message, state: FSMContext) -> None:
    inn = message.text.strip()
    digits = inn.replace(" ", "")
    if not digits.isdigit() or not (8 <= len(digits) <= 16):
        await message.answer("ИНН должен состоять из цифр (8–16). Попробуйте ещё раз.")
        return
    await state.update_data(inn=digits)
    await state.set_state(Registration.selfie)
    await message.answer("Шаг 4 из 4. Пришлите ваше селфи (фотографию).")


@router.message(Registration.selfie, F.photo)
async def reg_selfie(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    data = await state.get_data()
    code = data["invite_code"]
    car_id = data["car_id"]

    # Повторно валидируем приглашение и занятость машины на момент завершения:
    # пока водитель заполнял форму, машину мог занять другой или ссылка могла
    # протухнуть. Причину отказа называем ту же, что и на входе.
    check = await invitations_service.resolve_invitation(session, code)
    if not check.ok:
        await state.clear()
        await message.answer(INVITE_PROBLEM_TEXT[check.problem])
        return

    invitation = check.invitation
    car = await cars_service.get_car(session, car_id)

    file_id = message.photo[-1].file_id
    selfie_path = await save_telegram_file(
        bot, file_id, subdir="selfies", name=str(message.from_user.id)
    )

    driver = await drivers_service.register_driver(
        session,
        tg_user_id=message.from_user.id,
        full_name=data["full_name"],
        phone=data["phone"],
        inn=data["inn"],
        selfie_file_id=file_id,
        selfie_path=selfie_path,
        car_id=car_id,
    )
    await invitations_service.mark_used(session, invitation, message.from_user.id)
    await state.clear()

    await message.answer(
        "✅ Регистрация завершена!\n"
        f"ФИО: {driver.full_name}\n"
        f"Машина: {car.plate}\n\n"
        "Теперь вы можете вносить платежи. Ожидайте график от владельца."
    )

    # Уведомление администратору-создателю приглашения (FR-REG-4).
    await _notify_admin(bot, invitation.created_by, driver, car.plate)


@router.message(Registration.selfie)
async def reg_selfie_invalid(message: Message) -> None:
    await message.answer("Нужна именно фотография. Пришлите селфи.")


async def _notify_admin(bot: Bot, admin_id: int, driver, car_plate: str) -> None:
    try:
        await bot.send_message(
            admin_id,
            "🆕 Новый водитель зарегистрирован:\n"
            f"ФИО: {driver.full_name}\n"
            f"Телефон: {driver.phone}\n"
            f"ИНН: {driver.inn}\n"
            f"Машина: {car_plate}",
        )
        if driver.selfie_file_id:
            await bot.send_photo(admin_id, driver.selfie_file_id, caption="Селфи водителя")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Не удалось уведомить админа %s: %s", admin_id, exc)
