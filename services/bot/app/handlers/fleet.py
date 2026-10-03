"""Телеметрия, трекер, штрафы и ТО в карточке машины (только админ)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardMarkup, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from app.callbacks import AlertCB, ArmCB, FleetCB
from app.client import ApiClient, ApiError
from app.fines_view import list_header
from app.keyboards.fines import fines_page
from app.filters import IsAdmin
from app.states.fleet import FineForm, MaintenanceForm, TrackerForm

log = logging.getLogger(__name__)

router = Router(name="fleet")

NO_DATA = "Данных с трекера пока нет."


def _fmt_age(seconds: int | None) -> str:
    if seconds is None:
        return "нет данных"
    if seconds < 60:
        return f"{seconds} с назад"
    if seconds < 3600:
        return f"{seconds // 60} мин назад"
    return f"{seconds // 3600} ч назад"


def state_text(state: dict) -> str:
    """Онлайн показываем по свежести точки — хранимого флага у нас нет."""
    if not state.get("last_ts"):
        return NO_DATA

    online = "🟢 на связи" if state.get("online") else "🔴 нет связи"
    lines = [
        f"{online}, последняя точка: {_fmt_age(state.get('last_point_age_seconds'))}",
    ]
    if state.get("lat") is not None and state.get("lon") is not None:
        lines.append(f"Координаты: {state['lat']:.5f}, {state['lon']:.5f}")
    speed = state.get("speed_knots")
    if speed is not None:
        lines.append(f"Скорость: {float(speed) * 1.852:.0f} км/ч")
    if state.get("ignition") is not None:
        lines.append("Зажигание: " + ("включено" if state["ignition"] else "выключено"))
    if state.get("odometer_km") is not None:
        trust = "" if state.get("odometer_trusted", True) else " (недостоверен)"
        lines.append(f"Пробег по трекеру: {state['odometer_km']} км{trust}")
    if state.get("engine_blocked"):
        lines.append("⛔ Двигатель заблокирован")
    return "\n".join(lines)


def state_keyboard(
    car_id: int, state: dict, armed_cmd_id: int | None = None
) -> InlineKeyboardMarkup:
    """Переключатель прямо из карточки: заглушить по требованию, вернуть или
    снять взведённое ожидание.

    Блокировку выводим всегда (не только по алерту) — угон, невозврат, ДТП не
    завязаны на правила. Фактическую безопасность держит гейт в core-api:
    едущую/офлайн-машину он сразу не заглушит, а взводит ожидание.
    """
    builder = InlineKeyboardBuilder()
    if state.get("engine_blocked"):
        builder.button(
            text="Разблокировать двигатель",
            callback_data=AlertCB(action="unblock", alert_id=0, car_id=car_id),
        )
    elif armed_cmd_id:
        # Блокировка взведена и ждёт остановки — даём снять её прямо отсюда
        # (срока у взвода нет, поэтому отмена должна быть под рукой).
        builder.button(
            text="Отменить ожидание блокировки",
            callback_data=ArmCB(action="cancel", car_id=car_id, cmd_id=armed_cmd_id),
        )
    else:
        # Не напрямую в блокировку — через подтверждение (action="block_ask").
        builder.button(
            text="⛔ Заблокировать двигатель",
            callback_data=AlertCB(action="block_ask", alert_id=0, car_id=car_id),
        )
    return builder.as_markup()


def _armed_command_id(commands: list[dict]) -> int | None:
    """id взведённой (ожидающей) блокировки у машины, если есть."""
    for command in commands:
        if command.get("status") == "armed" and command.get("type") == "engine_stop":
            return command.get("id")
    return None


@router.callback_query(FleetCB.filter(F.action == "state"), IsAdmin)
async def show_state(
    query: CallbackQuery, callback_data: FleetCB, api: ApiClient
) -> None:
    try:
        state = await api.car_state(callback_data.car_id)
    except ApiError as exc:
        await query.message.answer(exc.human)
        await query.answer()
        return

    # Взведённая блокировка не хранится в car_state — спрашиваем журнал команд.
    armed_cmd_id = None
    try:
        armed_cmd_id = _armed_command_id(await api.commands(callback_data.car_id) or [])
    except ApiError as exc:
        log.warning("не удалось прочитать команды машины %s: %s", callback_data.car_id, exc)

    text = state_text(state)
    if armed_cmd_id and not state.get("engine_blocked"):
        text += "\n⏳ Блокировка взведена — сработает, когда машина встанет"

    await query.message.answer(
        text,
        reply_markup=state_keyboard(callback_data.car_id, state, armed_cmd_id),
    )
    await query.answer()


@router.callback_query(FleetCB.filter(F.action == "tracker"), IsAdmin)
async def tracker_menu(
    query: CallbackQuery, callback_data: FleetCB, api: ApiClient, state: FSMContext
) -> None:
    try:
        tracker = await api.get_tracker(callback_data.car_id)
    except ApiError as exc:
        await query.message.answer(exc.human)
        await query.answer()
        return

    current = (
        f"Сейчас привязан: {tracker['external_id']}" if tracker else "Трекер не привязан."
    )
    await state.set_state(TrackerForm.external_id)
    await state.update_data(car_id=callback_data.car_id)
    await query.message.answer(
        f"{current}\n\nПришлите идентификатор устройства (uniqueId из Traccar, 10 цифр).\n"
        "Внимание: смена трекера сбрасывает базу пробега — после неё отметьте ТО заново."
    )
    await query.answer()


@router.message(TrackerForm.external_id, IsAdmin)
async def tracker_set(message: Message, api: ApiClient, state: FSMContext) -> None:
    external_id = (message.text or "").strip()
    if not external_id.isdigit():
        await message.answer("Идентификатор — только цифры. Пришлите ещё раз.")
        return

    data = await state.get_data()
    await state.clear()
    try:
        tracker = await api.set_tracker(data["car_id"], external_id=external_id)
    except ApiError as exc:
        await message.answer(f"Не удалось привязать: {exc.human}")
        return
    await message.answer(
        f"Трекер {tracker['external_id']} привязан. База пробега сброшена — "
        "отметьте выполненное ТО, чтобы счётчик снова считался достоверным."
    )


@router.callback_query(FleetCB.filter(F.action == "fines"), IsAdmin)
async def fines_list(
    query: CallbackQuery, callback_data: FleetCB, api: ApiClient
) -> None:
    try:
        fines = await api.fines(callback_data.car_id)
    except ApiError as exc:
        await query.message.answer(exc.human)
        await query.answer()
        return

    if not fines:
        await query.message.answer(
            "Штрафов нет.",
            reply_markup=InlineKeyboardBuilder()
            .button(
                text="➕ Добавить штраф",
                callback_data=FleetCB(action="fine_add", car_id=callback_data.car_id),
            )
            .as_markup(),
        )
        await query.answer()
        return

    # Данные штрафа — на кнопке: одно касание открывает карточку, где и
    # отмечают оплату. Подписи «Оплачен: 300» у двух одинаковых штрафов были
    # неразличимы, а связи со строкой списка не было вовсе.
    markup, index, pages = fines_page(
        fines,
        scope="car",
        ref_id=callback_data.car_id,
        extra=[
            (
                "➕ Добавить штраф",
                FleetCB(action="fine_add", car_id=callback_data.car_id),
            )
        ],
    )
    await query.message.answer(
        list_header("Штрафы", fines, index, pages), reply_markup=markup
    )
    await query.answer()


@router.callback_query(FleetCB.filter(F.action == "fine_add"), IsAdmin)
async def fine_add(
    query: CallbackQuery, callback_data: FleetCB, state: FSMContext
) -> None:
    await state.set_state(FineForm.amount)
    await state.update_data(car_id=callback_data.car_id)
    await query.message.answer("Сумма штрафа (только число):")
    await query.answer()


@router.message(FineForm.amount, IsAdmin)
async def fine_amount(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").replace(",", ".").replace(" ", "").strip()
    try:
        amount = Decimal(raw)
    except (InvalidOperation, ValueError):
        await message.answer("Не похоже на сумму. Пришлите число, например 3000")
        return

    await state.update_data(amount=str(amount))
    await state.set_state(FineForm.issued_at)
    await message.answer(
        "Дата нарушения в формате ДД.ММ.ГГГГ (или «сегодня»).\n"
        "Она важна: правило считает штрафы за окно именно по этой дате."
    )


@router.message(FineForm.issued_at, IsAdmin)
async def fine_issued_at(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip().lower()
    if raw in {"сегодня", "-"}:
        issued_at = datetime.now(timezone.utc)
    else:
        try:
            issued_at = datetime.strptime(raw, "%d.%m.%Y").replace(tzinfo=timezone.utc)
        except ValueError:
            await message.answer("Дата не разобрана. Пришлите ДД.ММ.ГГГГ или «сегодня».")
            return

    await state.update_data(issued_at=issued_at.isoformat())
    await state.set_state(FineForm.note)
    await message.answer("Номер постановления и заметка одной строкой (или «-», если нет).")


@router.message(FineForm.note, IsAdmin)
async def fine_note(message: Message, api: ApiClient, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    note = None if raw in {"-", ""} else raw
    # Первое «слово» похоже на номер постановления — кладём его отдельным полем.
    external_ref = None
    if note:
        head = note.split()[0]
        if any(ch.isdigit() for ch in head) and len(head) <= 64:
            external_ref = head

    data = await state.get_data()
    await state.clear()
    try:
        await api.add_fine(
            data["car_id"],
            tg_id=message.from_user.id,
            amount=data["amount"],
            issued_at=data["issued_at"],
            external_ref=external_ref,
            note=note,
        )
    except ApiError as exc:
        await message.answer(f"Не удалось добавить штраф: {exc.human}")
        return
    await message.answer("Штраф добавлен. Когда оплатят — отметьте его оплаченным.")


@router.callback_query(FleetCB.filter(F.action == "maint"), IsAdmin)
async def maintenance_menu(
    query: CallbackQuery, callback_data: FleetCB, api: ApiClient
) -> None:
    try:
        items = await api.maintenance(callback_data.car_id)
    except ApiError as exc:
        await query.message.answer(exc.human)
        await query.answer()
        return

    if not items:
        text = "ТО не настроено."
    else:
        rows = []
        for item in items:
            over = item.get("over_km")
            due = "" if over is None else f", перепробег {over} км"
            rows.append(
                f"• {item['type']}: интервал {item['interval_km']} км, "
                f"база {item['last_service_km']} км{due}"
            )
        text = "Обслуживание:\n" + "\n".join(rows)

    builder = InlineKeyboardBuilder()
    builder.button(
        text="🔧 Задать интервал",
        callback_data=FleetCB(action="maint_set", car_id=callback_data.car_id),
    )
    builder.button(
        text="✅ ТО выполнено",
        callback_data=FleetCB(action="maint_done", car_id=callback_data.car_id),
    )
    builder.adjust(1)
    await query.message.answer(text, reply_markup=builder.as_markup())
    await query.answer()


@router.callback_query(FleetCB.filter(F.action == "maint_set"), IsAdmin)
async def maintenance_set(
    query: CallbackQuery, callback_data: FleetCB, state: FSMContext
) -> None:
    await state.set_state(MaintenanceForm.interval)
    await state.update_data(car_id=callback_data.car_id)
    await query.message.answer("Интервал ТО в километрах (например, 10000):")
    await query.answer()


@router.message(MaintenanceForm.interval, IsAdmin)
async def maintenance_interval(
    message: Message, api: ApiClient, state: FSMContext
) -> None:
    raw = (message.text or "").replace(",", ".").strip()
    try:
        interval = Decimal(raw)
    except (InvalidOperation, ValueError):
        await message.answer("Пришлите число километров, например 10000")
        return

    data = await state.get_data()
    await state.clear()
    try:
        await api.set_maintenance(
            data["car_id"],
            tg_id=message.from_user.id,
            type="oil",
            interval_km=str(interval),
        )
    except ApiError as exc:
        await message.answer(f"Не удалось сохранить: {exc.human}")
        return
    await message.answer(f"Интервал ТО {interval} км сохранён.")


@router.callback_query(FleetCB.filter(F.action == "maint_done"), IsAdmin)
async def maintenance_done(
    query: CallbackQuery, callback_data: FleetCB, api: ApiClient
) -> None:
    try:
        await api.maintenance_done(callback_data.car_id, "oil", tg_id=query.from_user.id)
    except ApiError as exc:
        await query.answer(exc.human, show_alert=True)
        return
    await query.message.answer(
        "ТО отмечено: база пробега перенесена на текущий счётчик трекера."
    )
    await query.answer()
