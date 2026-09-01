"""Управление автопарком (только админ): список, добавление с фото, удаление."""
from __future__ import annotations

from aiogram import Bot, F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import ReplyKeyboardBuilder
from sqlalchemy.ext.asyncio import AsyncSession

from bot.callbacks import CarCB
from bot.db.models import CarStatus
from bot.filters import IsAdmin
from bot.keyboards.admin import BTN_CARS, admin_menu
from bot.keyboards.cars import (
    BTN_ADD_CAR,
    car_delete_confirm_kb,
    car_detail_kb,
    car_status_label,
    cars_list_kb,
)
from bot.services import cars as cars_service
from bot.services.storage import save_telegram_file
from bot.states.car import AddCar

router = Router(name="cars")
router.message.filter(IsAdmin)
router.callback_query.filter(IsAdmin)


async def _show_cars_list(message: Message, session: AsyncSession) -> None:
    cars = await cars_service.list_cars(session)
    if not cars:
        await message.answer(
            "В автопарке пока нет машин.\n"
            f"Нажмите «{BTN_ADD_CAR}», чтобы добавить первую."
        )
        return
    await message.answer(
        f"🚗 Автопарк — {len(cars)} машин(ы):", reply_markup=cars_list_kb(cars)
    )


@router.message(F.text == BTN_CARS)
async def open_cars(message: Message, session: AsyncSession) -> None:
    await message.answer(
        "Раздел «Автопарк». Выберите машину или добавьте новую.",
        reply_markup=_add_car_reply_kb(),
    )
    await _show_cars_list(message, session)


def _add_car_reply_kb():
    b = ReplyKeyboardBuilder()
    b.button(text=BTN_ADD_CAR)
    b.button(text="⬅️ В меню")
    b.adjust(2)
    return b.as_markup(resize_keyboard=True)


@router.message(F.text == "⬅️ В меню")
async def back_to_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Главное меню.", reply_markup=admin_menu())


# ---------------------------------------------------------------- Добавление
@router.message(F.text == BTN_ADD_CAR)
async def add_car_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AddCar.plate)
    await message.answer(
        "Введите гос. номер машины (например, 01A123BC).\n"
        "Для отмены отправьте /cancel."
    )


@router.message(AddCar.plate, F.text)
async def add_car_plate(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    plate = message.text.strip().upper()
    if await cars_service.plate_exists(session, plate):
        await message.answer("Машина с таким номером уже есть. Введите другой номер.")
        return
    await state.update_data(plate=plate)
    await state.set_state(AddCar.model)
    await message.answer(
        "Введите марку/модель (например, Chevrolet Cobalt).\n"
        "Или отправьте «-», чтобы пропустить."
    )


@router.message(AddCar.model, F.text)
async def add_car_model(message: Message, state: FSMContext) -> None:
    model = message.text.strip()
    if model == "-":
        model = None
    await state.update_data(model=model)
    await state.set_state(AddCar.photo)
    await message.answer("Пришлите фотографию машины.")


@router.message(AddCar.photo, F.photo)
async def add_car_photo(
    message: Message, state: FSMContext, session: AsyncSession, bot: Bot
) -> None:
    file_id = message.photo[-1].file_id
    data = await state.get_data()
    plate = data["plate"]

    photo_path = await save_telegram_file(
        bot, file_id, subdir="cars", name=plate.replace("/", "_")
    )
    car = await cars_service.create_car(
        session,
        plate=plate,
        model=data.get("model"),
        photo_file_id=file_id,
        photo_path=photo_path,
    )
    await state.clear()
    await message.answer(
        f"✅ Машина добавлена: {car.plate}"
        + (f" · {car.model}" if car.model else "")
        + f"\nСтатус: {car_status_label(car.status)}",
        reply_markup=admin_menu(),
    )


@router.message(AddCar.photo)
async def add_car_photo_invalid(message: Message) -> None:
    await message.answer("Нужна именно фотография. Пришлите фото машины.")


# ---------------------------------------------------------------- Просмотр
@router.callback_query(CarCB.filter(F.action == "view"))
async def view_car(
    query: CallbackQuery, callback_data: CarCB, session: AsyncSession
) -> None:
    # car_id == 0 — возврат к списку
    if callback_data.car_id == 0:
        cars = await cars_service.list_cars(session)
        await query.message.answer(
            f"🚗 Автопарк — {len(cars)} машин(ы):", reply_markup=cars_list_kb(cars)
        )
        await query.answer()
        return

    car = await cars_service.get_car(session, callback_data.car_id)
    if car is None:
        await query.answer("Машина не найдена.", show_alert=True)
        return

    caption = (
        f"🚗 <b>{car.plate}</b>\n"
        f"Модель: {car.model or '—'}\n"
        f"Статус: {car_status_label(car.status)}"
    )
    if car.photo_file_id:
        await query.message.answer_photo(
            car.photo_file_id, caption=caption, reply_markup=car_detail_kb(car)
        )
    else:
        await query.message.answer(caption, reply_markup=car_detail_kb(car))
    await query.answer()


# ---------------------------------------------------------------- Удаление
@router.callback_query(CarCB.filter(F.action == "delete"))
async def delete_car_ask(
    query: CallbackQuery, callback_data: CarCB, session: AsyncSession
) -> None:
    car = await cars_service.get_car(session, callback_data.car_id)
    if car is None:
        await query.answer("Машина не найдена.", show_alert=True)
        return
    if car.status == CarStatus.occupied:
        await query.answer(
            "Нельзя удалить занятую машину — сначала открепите водителя.",
            show_alert=True,
        )
        return
    await query.message.answer(
        f"Удалить машину {car.plate}?", reply_markup=car_delete_confirm_kb(car)
    )
    await query.answer()


@router.callback_query(CarCB.filter(F.action == "delete_confirm"))
async def delete_car_confirm(
    query: CallbackQuery, callback_data: CarCB, session: AsyncSession
) -> None:
    car = await cars_service.get_car(session, callback_data.car_id)
    if car is None:
        await query.answer("Машина не найдена.", show_alert=True)
        return
    if car.status == CarStatus.occupied:
        await query.answer("Машина занята, удаление отменено.", show_alert=True)
        return
    plate = car.plate
    await cars_service.delete_car(session, car)
    await query.message.answer(f"🗑 Машина {plate} удалена.")
    await query.answer()
