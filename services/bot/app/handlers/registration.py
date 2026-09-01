"""Регистрация водителя по одноразовой ссылке-приглашению (FR-INV-4, FR-REG-1..4)."""
from __future__ import annotations

import logging

from aiogram import Bot, F, Router
from aiogram.filters import CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import Message, ReplyKeyboardRemove

from app.client import ApiClient, ApiError
from app.config import settings
from app.keyboards.admin import admin_menu
from app.keyboards.drivers import share_phone_kb
from app.middlewares.role import Role, RoleMiddleware
from app.states.registration import Registration

logger = logging.getLogger(__name__)
router = Router(name="registration")

_ASK_OWNER = "Обратитесь к владельцу автопарка за новой ссылкой."

# Отказ объясняем по существу: «ссылка протухла» и «машину занял другой» —
# разные ситуации, и водителю от них нужно разное. Ключи — значения
# InviteProblem из core-api (contracts.InviteCheckDTO.problem), plain str.
INVITE_PROBLEM_TEXT = {
    "not_found": f"Ссылка-приглашение недействительна. {_ASK_OWNER}",
    "expired": f"Срок действия ссылки-приглашения истёк. {_ASK_OWNER}",
    "used": f"По этой ссылке уже зарегистрирован другой водитель. {_ASK_OWNER}",
    "car_taken": f"Это транспортное средство уже занято другим водителем. {_ASK_OWNER}",
}
_INVITE_PROBLEM_FALLBACK = f"Ссылка-приглашение недействительна. {_ASK_OWNER}"


def _invite_problem_text(problem: str | None) -> str:
    return INVITE_PROBLEM_TEXT.get(problem, _INVITE_PROBLEM_FALLBACK)


def _already_registered_text(driver: dict) -> str:
    """Водителю важно понимать ПОЧЕМУ нельзя: он уже за машиной, а не «просто зарегистрирован»."""
    plate = driver.get("car_plate")
    plate_part = f" <b>{plate}</b>" if plate else ""
    return (
        f"Вы уже закреплены за транспортным средством{plate_part}. "
        "Повторная регистрация не нужна."
    )


@router.message(CommandStart(deep_link=True))
async def start_by_invite(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    api: ApiClient,
    role: Role,
    driver: dict | None = None,
) -> None:
    if role is Role.admin:
        await message.answer("Вы администратор.", reply_markup=admin_menu())
        return
    if role is Role.driver:
        await message.answer(_already_registered_text(driver or {}))
        return

    code = command.args or ""
    try:
        check = await api.resolve_invitation(code)
    except ApiError as exc:
        await message.answer(exc.human)
        return
    if not check.get("ok"):
        await message.answer(_invite_problem_text(check.get("problem")))
        return

    car_title = check.get("car_plate") or "—"

    await state.clear()
    await state.update_data(invite_code=code)
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
    message: Message,
    state: FSMContext,
    api: ApiClient,
    role_mw: RoleMiddleware,
    bot: Bot,
) -> None:
    data = await state.get_data()
    code = data["invite_code"]

    # Повторно валидируем приглашение и занятость машины на момент завершения:
    # пока водитель заполнял форму, машину мог занять другой или ссылка могла
    # протухнуть. Причину отказа называем ту же, что и на входе.
    try:
        check = await api.resolve_invitation(code)
    except ApiError as exc:
        await message.answer(exc.human)
        return
    if not check.get("ok"):
        await state.clear()
        await message.answer(_invite_problem_text(check.get("problem")))
        return

    car_plate = check.get("car_plate") or "—"
    # Файла на диске у бота больше нет — в API уходит только file_id.
    file_id = message.photo[-1].file_id

    try:
        driver = await api.register_driver(
            code=code,
            tg_user_id=message.from_user.id,
            full_name=data["full_name"],
            phone=data["phone"],
            inn=data["inn"],
            selfie_file_id=file_id,
        )
    except ApiError as exc:
        await state.clear()
        # 409 от core-api несёт код причины отказа (problem) прямо в detail.
        await message.answer(_invite_problem_text(exc.detail) if exc.status_code == 409 else exc.human)
        return

    await state.clear()
    role_mw.invalidate(message.from_user.id)

    await message.answer(
        "✅ Регистрация завершена!\n"
        f"ФИО: {driver['full_name']}\n"
        f"Машина: {car_plate}\n\n"
        "Теперь вы можете вносить платежи. Ожидайте график от владельца."
    )

    # Уведомление администраторам о новом водителе (FR-REG-4). Кто именно
    # выдал приглашение, API боту не сообщает — оповещаем всех админов.
    await _notify_admins(bot, driver, car_plate)


@router.message(Registration.selfie)
async def reg_selfie_invalid(message: Message) -> None:
    await message.answer("Нужна именно фотография. Пришлите селфи.")


async def _notify_admins(bot: Bot, driver: dict, car_plate: str) -> None:
    text = (
        "🆕 Новый водитель зарегистрирован:\n"
        f"ФИО: {driver['full_name']}\n"
        f"Телефон: {driver.get('phone') or '—'}\n"
        f"ИНН: {driver.get('inn') or '—'}\n"
        f"Машина: {car_plate}"
    )
    for admin_id in settings.admin_ids:
        try:
            await bot.send_message(admin_id, text)
            if driver.get("selfie_file_id"):
                await bot.send_photo(
                    admin_id, driver["selfie_file_id"], caption="Селфи водителя"
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Не удалось уведомить админа %s: %s", admin_id, exc)
