"""Экраны штрафов: списки, карточка, проверка по кнопке.

Данные берутся из нашей БД. В tolom ходит только фоновая задача — по
расписанию или по кнопке «Проверить сейчас»; открытие карточки в сервис не
ходит никогда.
"""
from __future__ import annotations

import asyncio
import logging

from aiogram import F, Router
from aiogram.filters.callback_data import CallbackData
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks import FineCB
from app.client import ApiClient, ApiError
from app.filters import IsAdmin, IsAdminOrDriver, IsDriver
from app.fines_view import fine_card_text, list_header
from app.keyboards.admin import BTN_FINES
from app.keyboards.driver_menu import BTN_MY_FINES
from app.keyboards.fines import fines_page

log = logging.getLogger(__name__)
router = Router(name="fines")

# Сколько ждём результат проверки, прежде чем отпустить человека. Обход парка
# держит паузу между номерами, поэтому счёт идёт на десятки секунд.
CHECK_POLL_SECONDS = 3
CHECK_WAIT_SECONDS = 60


async def _show_list(
    message: Message,
    fines: list[dict],
    *,
    title: str,
    scope: str,
    ref_id: int = 0,
    page: int = 0,
    with_plate: bool = False,
    extra: list[tuple[str, CallbackData]] | None = None,
    edit: bool = False,
) -> None:
    if not fines:
        text = f"{title}: пусто."
        markup = None
    else:
        markup, index, pages = fines_page(
            fines,
            scope=scope,
            ref_id=ref_id,
            page=page,
            with_plate=with_plate,
            extra=extra,
        )
        text = list_header(title, fines, index, pages)
    if edit:
        await message.edit_text(text, reply_markup=markup)
    else:
        await message.answer(text, reply_markup=markup)


# --- списки -----------------------------------------------------------------


@router.message(F.text == BTN_FINES, IsAdmin)
async def fleet_fines(message: Message, api: ApiClient) -> None:
    try:
        fines = await api.fleet_fines(tg_id=message.from_user.id)
    except ApiError as exc:
        await message.answer(exc.human)
        return
    await _show_list(
        message,
        fines,
        title="Неоплаченные штрафы парка",
        scope="fleet",
        with_plate=True,
        extra=[("🔄 Проверить сейчас", FineCB(action="check"))],
    )


@router.message(F.text == BTN_MY_FINES, IsDriver)
async def my_fines(message: Message, api: ApiClient, driver: dict) -> None:
    car_id = driver.get("car_id")
    if not car_id:
        await message.answer("За вами не закреплена машина — штрафов нет.")
        return
    try:
        fines = await api.fines(car_id)
    except ApiError as exc:
        await message.answer(exc.human)
        return
    await _show_list(
        message, fines, title="Ваши штрафы", scope="mine", ref_id=car_id
    )


# --- листание ---------------------------------------------------------------


# Области, которые показывают чужие машины, — только для админа. Своя машина
# берётся из роли, а не из callback_data: значение оттуда контролирует клиент.
ADMIN_SCOPES = {"fleet", "car", "alert"}

TITLES = {
    "fleet": "Неоплаченные штрафы парка",
    "mine": "Ваши штрафы",
    "alert": "Новые штрафы",
    "car": "Штрафы",
}


def own_car_id(driver: dict | None) -> int | None:
    return (driver or {}).get("car_id")


async def _fines_of_scope(
    api: ApiClient,
    scope: str,
    ref_id: int,
    *,
    actor: int,
    own_car: int | None,
) -> list[dict]:
    """Один и тот же список, что и при открытии экрана: страницы обязаны совпадать.

    Для «своих» штрафов `ref_id` из callback_data не используется вовсе —
    машина берётся из роли водителя. Сверять клиентское значение с настоящим
    бессмысленно там, где настоящее и так под рукой.
    """
    if scope == "mine":
        return await api.fines(own_car) if own_car else []
    if scope == "fleet":
        return await api.fleet_fines(tg_id=actor)
    if scope == "alert":
        alert = await api.alert(ref_id)
        ids = (alert.get("payload") or {}).get("fine_ids") or []
        fines = await api.fines(alert["car_id"])
        by_id = {f["id"]: f for f in fines}
        return [by_id[i] for i in ids if i in by_id]
    return await api.fines(ref_id)


@router.callback_query(FineCB.filter(F.action == "page"), IsAdminOrDriver)
async def turn_page(
    callback: CallbackQuery,
    callback_data: FineCB,
    api: ApiClient,
    role: str,
    driver: dict | None = None,
) -> None:
    scope = callback_data.scope
    if scope in ADMIN_SCOPES and role != "admin":
        # Иначе водитель (и любой, кто соберёт callback_data руками) листал бы
        # неоплаченные штрафы всего парка.
        await callback.answer("Недоступно", show_alert=True)
        return
    try:
        fines = await _fines_of_scope(
            api,
            scope,
            callback_data.ref_id,
            actor=callback.from_user.id,
            own_car=own_car_id(driver),
        )
    except ApiError as exc:
        await callback.answer(exc.human, show_alert=True)
        return
    await _show_list(
        callback.message,
        fines,
        title=TITLES.get(scope, "Штрафы"),
        scope=scope,
        ref_id=callback_data.ref_id,
        page=callback_data.page,
        with_plate=scope in {"fleet", "alert"},
        edit=True,
    )
    await callback.answer()


# --- карточка ---------------------------------------------------------------


@router.callback_query(FineCB.filter(F.action == "card"), IsAdminOrDriver)
async def show_card(
    callback: CallbackQuery,
    callback_data: FineCB,
    api: ApiClient,
    role: str,
    driver: dict | None = None,
) -> None:
    try:
        fine = await api.fine(callback_data.fine_id)
    except ApiError as exc:
        await callback.answer(exc.human, show_alert=True)
        return

    if role != "admin" and fine["car_id"] != own_car_id(driver):
        # Машина водителя берётся из роли: `ref_id` из callback_data клиент
        # подставит любой, и сверка с ним ничего не доказывает.
        await callback.answer("Штраф не найден", show_alert=True)
        return

    builder = InlineKeyboardBuilder()
    if role == "admin" and fine["status"] != "paid":
        builder.button(
            text="✅ Отметить оплаченным",
            callback_data=FineCB(
                action="pay",
                fine_id=fine["id"],
                scope=callback_data.scope,
                ref_id=callback_data.ref_id,
                page=callback_data.page,
            ),
        )
    builder.button(
        text="⬅️ К списку",
        callback_data=FineCB(
            action="page",
            scope=callback_data.scope,
            ref_id=callback_data.ref_id,
            page=callback_data.page,
        ),
    )
    builder.adjust(1)
    await callback.message.edit_text(
        fine_card_text(fine), reply_markup=builder.as_markup()
    )
    await callback.answer()


@router.callback_query(FineCB.filter(F.action == "pay"), IsAdmin)
async def pay(callback: CallbackQuery, callback_data: FineCB, api: ApiClient) -> None:
    try:
        fine = await api.pay_fine(callback_data.fine_id, tg_id=callback.from_user.id)
    except ApiError as exc:
        await callback.answer(exc.human, show_alert=True)
        return
    await callback.message.edit_text(fine_card_text(fine))
    await callback.answer("Отмечен оплаченным")


# --- проверка по кнопке -----------------------------------------------------


@router.callback_query(FineCB.filter(F.action == "check"), IsAdmin)
async def check_now(callback: CallbackQuery, api: ApiClient) -> None:
    actor = callback.from_user.id
    try:
        run = await api.check_fines(tg_id=actor)
    except ApiError as exc:
        await callback.answer(exc.human, show_alert=True)
        return
    await callback.answer("Проверяю парк…")
    status = await callback.message.answer("🔄 Проверяю штрафы по парку…")

    finished = await _wait_for_run(api, run["id"], actor)
    if finished is None:
        await status.edit_text(
            "Проверка идёт дольше обычного. Результат придёт уведомлением."
        )
        return
    await _report_run(status, api, finished, actor)


async def _wait_for_run(api: ApiClient, run_id: int, actor: int) -> dict | None:
    """Ждём именно свой прогон: его строка заведена ещё при постановке в очередь."""
    for _ in range(CHECK_WAIT_SECONDS // CHECK_POLL_SECONDS):
        await asyncio.sleep(CHECK_POLL_SECONDS)
        try:
            run = await api.task_run(run_id, tg_id=actor)
        except ApiError as exc:
            log.warning("не удалось прочитать прогон %s: %s", run_id, exc)
            continue
        if run.get("finished_at"):
            return run
    return None


async def _report_run(
    message: Message, api: ApiClient, run: dict, actor: int
) -> None:
    payload = run.get("payload") or {}
    if run.get("status") != "ok":
        # Отказ сервиса не должен выглядеть как «штрафов нет».
        await message.edit_text(f"Проверка не удалась: {run.get('detail') or 'нет деталей'}")
        return

    new_ids = payload.get("new_fine_ids") or []
    if not new_ids:
        await message.edit_text(f"Готово: {run.get('detail')}. Новых штрафов нет.")
        return

    # Один списочный запрос, а не по запросу на штраф: за первый прогон по
    # парку их бывают десятки, и это столько же лишних round-trip. Новый
    # штраф по определению неоплачен, поэтому он есть в этом списке.
    wanted = set(new_ids)
    try:
        fines = [
            f for f in await api.fleet_fines(tg_id=actor) if f["id"] in wanted
        ]
    except ApiError as exc:
        log.warning("список новых штрафов не прочитан: %s", exc)
        await message.edit_text(f"Готово: {run.get('detail')}.")
        return
    await _show_list(
        message,
        fines,
        title="Новые штрафы",
        scope="fleet",
        with_plate=True,
        edit=True,
    )
