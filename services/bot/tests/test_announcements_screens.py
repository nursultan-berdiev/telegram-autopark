"""Объявления: история для обеих ролей, публикация админом, удаление."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.callbacks import AnnouncementCB
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText

from app.client import ApiError
from app.handlers import announcements as screens
from app.keyboards.announcements import PAGE_SIZE, button_label
from app.middlewares.role import Role
from app.states.announcement import AnnouncementForm
from contracts import ANNOUNCEMENT_MAX_TEXT, render_announcement
from tests.conftest import ADMIN_ID, FakeApi


def _ann(ann_id: int, text: str = "Скидка 1500 сом", **over) -> dict:
    item = {
        "id": ann_id,
        "text": text,
        "created_by": ADMIN_ID,
        "recipients": 3,
        "created_at": "2026-10-17T05:00:00+00:00",
    }
    item.update(over)
    return item


@dataclass
class Msg:
    answers: list[str] = field(default_factory=list)
    edits: list[str] = field(default_factory=list)
    markups: list[Any] = field(default_factory=list)
    text: str | None = None
    from_user: Any = None

    async def answer(self, text: str, **kwargs: Any) -> "Msg":
        self.answers.append(text)
        self.markups.append(kwargs.get("reply_markup"))
        return self

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.edits.append(text)
        self.markups.append(kwargs.get("reply_markup"))

    async def edit_reply_markup(self, **kwargs: Any) -> None:
        self.markups.append(kwargs.get("reply_markup"))


@dataclass
class User:
    id: int = ADMIN_ID


@dataclass
class Callback:
    message: Msg = field(default_factory=Msg)
    from_user: User = field(default_factory=User)
    answers: list[str | None] = field(default_factory=list)

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        self.answers.append(text)


class State:
    def __init__(self) -> None:
        self.state = None
        self.data: dict = {}

    async def set_state(self, state) -> None:
        self.state = state

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def get_data(self) -> dict:
        return dict(self.data)

    async def clear(self) -> None:
        self.state, self.data = None, {}


def _labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row] if markup else []


# --- история ----------------------------------------------------------------


async def test_driver_sees_history_without_admin_actions():
    message = Msg()
    api = FakeApi(announcements=[_ann(2, "Новые правила"), _ann(1)])

    await screens.open_history(message, api, Role.driver)

    assert message.answers == ["📢 Объявления: 2"]
    labels = _labels(message.markups[0])
    assert labels == ["17.10 · Новые правила", "17.10 · Скидка 1500 сом"]


async def test_admin_can_start_new_announcement_even_from_empty_history():
    message = Msg()
    api = FakeApi(announcements=[])

    await screens.open_history(message, api, Role.admin)

    assert message.answers == ["📢 Объявлений пока нет."]
    assert _labels(message.markups[0]) == ["➕ Новое объявление"]


async def test_empty_history_for_driver_has_no_keyboard():
    message = Msg()

    await screens.open_history(message, FakeApi(announcements=[]), Role.driver)

    assert message.markups == [None]


async def test_history_pages():
    items = [_ann(i, f"Объявление {i}") for i in range(PAGE_SIZE + 2, 0, -1)]
    api = FakeApi(announcements=items)
    message = Msg()

    await screens.open_history(message, api, Role.driver)
    first = _labels(message.markups[0])
    assert len(first) == PAGE_SIZE + 1 and first[-1] == "Далее ▶"
    assert "страница 1/2" in message.answers[0]

    callback = Callback(message=Msg())
    await screens.turn_page(callback, AnnouncementCB(action="page", page=1), api, Role.driver)
    second = _labels(callback.message.markups[0])
    assert second == ["17.10 · Объявление 2", "17.10 · Объявление 1", "◀ Назад"]


async def test_page_beyond_range_is_clamped():
    """Удаление последнего на странице не должно оставить экран без кнопок."""
    api = FakeApi(announcements=[_ann(1)])
    callback = Callback(message=Msg())

    await screens.turn_page(callback, AnnouncementCB(action="page", page=3), api, Role.driver)

    assert callback.message.edits == ["📢 Объявления: 1"]
    assert _labels(callback.message.markups[0]) == ["17.10 · Скидка 1500 сом"]


def test_label_uses_local_date_and_first_line():
    """UTC 20:30 16-го — это уже 17-е по Бишкеку."""
    label = button_label(
        _ann(1, "Первая строка\nвторая", created_at="2026-10-16T20:30:00+00:00")
    )
    assert label == "17.10 · Первая строка"
    long = button_label(_ann(1, "я" * 100))
    assert long.endswith("…") and len(long) < 60


async def test_driver_card_escapes_text_and_hides_admin_details():
    callback = Callback(message=Msg())
    api = FakeApi(announcement=_ann(5, "Аренда <1500> & скидка"))

    await screens.show_card(callback, AnnouncementCB(action="card", ann_id=5), api, Role.driver)

    text = callback.message.edits[0]
    assert "Аренда &lt;1500&gt; &amp; скидка" in text
    assert "Разослано" not in text
    assert _labels(callback.message.markups[0]) == ["⬅️ К списку"]


async def test_admin_card_shows_recipients_and_delete():
    callback = Callback(message=Msg())
    api = FakeApi(announcement=_ann(5))

    await screens.show_card(callback, AnnouncementCB(action="card", ann_id=5), api, Role.admin)

    assert "Разослано водителям: 3" in callback.message.edits[0]
    assert _labels(callback.message.markups[0]) == ["🗑 Удалить", "⬅️ К списку"]


async def test_missing_card_is_reported():
    callback = Callback(message=Msg())

    def _missing(*_a, **_k):
        raise ApiError(404, "объявление 5 не найдено")

    api = FakeApi(announcement=_missing)
    await screens.show_card(callback, AnnouncementCB(action="card", ann_id=5), api, Role.driver)

    assert callback.answers == ["объявление 5 не найдено"]
    assert callback.message.edits == []


# --- публикация -------------------------------------------------------------


async def test_publish_flow_preview_then_send():
    state = State()
    callback = Callback(message=Msg())
    await screens.new_announcement(callback, state)
    assert state.state == AnnouncementForm.text

    message = Msg(text="  Скидка <1500> сом  ")
    await screens.take_text(message, state)
    assert state.state == AnnouncementForm.confirm
    # Предпросмотр — ровно то, что core-api положит в очередь (общий рендер).
    assert message.answers[-1] == render_announcement("Скидка <1500> сом")
    assert "Скидка &lt;1500&gt; сом" in message.answers[-1]
    assert _labels(message.markups[-1]) == ["✅ Отправить всем водителям", "❌ Отмена"]

    api = FakeApi(publish_announcement=_ann(9, recipients=4))
    send_cb = Callback(message=Msg())
    await screens.send(send_cb, state, api)

    ((args, kwargs),) = api.called("publish_announcement")
    assert kwargs == {"text": "Скидка <1500> сом", "tg_id": ADMIN_ID}
    assert state.state is None
    assert send_cb.message.markups[0] is None  # кнопки с предпросмотра сняты
    assert "Разошлём 4 водителям" in send_cb.message.answers[0]


async def test_send_failure_keeps_preview_usable():
    state = State()
    await state.set_state(AnnouncementForm.confirm)
    await state.update_data(text="текст")

    def _down(**_k):
        raise ApiError(503, "Сервер недоступен, попробуйте позже.")

    callback = Callback(message=Msg())
    await screens.send(callback, state, FakeApi(publish_announcement=_down))

    assert callback.answers == ["Сервер недоступен, попробуйте позже."]
    assert state.state == AnnouncementForm.confirm and state.data == {"text": "текст"}


async def test_publish_without_drivers_says_so():
    state = State()
    await state.set_state(AnnouncementForm.confirm)
    await state.update_data(text="текст")
    callback = Callback(message=Msg())

    await screens.send(callback, state, FakeApi(publish_announcement=_ann(9, recipients=0)))

    assert "рассылать некому" in callback.message.answers[0]


async def test_too_long_text_is_rejected_before_preview():
    state = State()
    await state.set_state(AnnouncementForm.text)
    message = Msg(text="я" * (ANNOUNCEMENT_MAX_TEXT + 1))

    await screens.take_text(message, state)

    assert state.state == AnnouncementForm.text
    assert "Слишком длинно" in message.answers[0]


async def test_emoji_count_double_like_in_telegram():
    state = State()
    await state.set_state(AnnouncementForm.text)
    message = Msg(text="🚗" * (ANNOUNCEMENT_MAX_TEXT // 2 + 1))

    await screens.take_text(message, state)

    assert state.state == AnnouncementForm.text
    assert f"{ANNOUNCEMENT_MAX_TEXT + 2} символов" in message.answers[0]


async def test_non_text_draft_is_rejected():
    message = Msg()

    await screens.take_text_invalid(message)

    assert "Нужен текст" in message.answers[0]


async def test_stale_preview_button_does_not_touch_state():
    callback = Callback(message=Msg())

    await screens.preview_stale(callback)

    assert "не действует" in callback.answers[0]
    assert callback.message.answers == []


async def test_cancel_drops_draft():
    state = State()
    await state.set_state(AnnouncementForm.confirm)
    await state.update_data(text="текст")
    callback = Callback(message=Msg())

    await screens.cancel(callback, state)

    assert state.state is None
    assert callback.message.answers == ["Объявление не отправлено."]


# --- удаление ---------------------------------------------------------------


async def test_ask_delete_swaps_keyboard_for_confirmation():
    callback = Callback(message=Msg())

    await screens.ask_delete(callback, AnnouncementCB(action="delete", ann_id=5, page=1))

    assert _labels(callback.message.markups[0]) == ["✅ Да, удалить из истории", "❌ Отмена"]
    assert "рассылка остановится" in callback.answers[0]


async def test_delete_hides_and_returns_to_history():
    api = FakeApi(announcements=[_ann(1)], delete_announcement=None)
    callback = Callback(message=Msg())

    await screens.delete(
        callback, AnnouncementCB(action="delete_confirm", ann_id=2, page=0), api
    )

    ((args, kwargs),) = api.called("delete_announcement")
    assert args == (2,) and kwargs == {"tg_id": ADMIN_ID}
    assert callback.answers == ["Удалено из истории"]
    assert callback.message.edits == ["📢 Объявления: 1"]


async def test_delete_failure_is_reported_and_list_kept():
    def _forbidden(*_a, **_k):
        raise ApiError(403, "действие доступно только админу")

    callback = Callback(message=Msg())
    await screens.delete(
        callback,
        AnnouncementCB(action="delete_confirm", ann_id=2),
        FakeApi(delete_announcement=_forbidden),
    )

    assert callback.answers == ["действие доступно только админу"]
    assert callback.message.edits == []


async def test_empty_text_is_rejected():
    state = State()
    await state.set_state(AnnouncementForm.text)
    message = Msg(text="   ")

    await screens.take_text(message, state)

    assert state.state == AnnouncementForm.text
    assert message.answers == ["Пустое объявление — пришлите текст."]


async def test_double_tap_on_same_page_is_quiet():
    """Повторный тап: тот же текст, Telegram отвечает «not modified» — это не ошибка."""

    class _Same(Msg):
        async def edit_text(self, text: str, **kwargs: Any) -> None:
            raise TelegramBadRequest(
                method=EditMessageText(text=text), message="Bad Request: message is not modified"
            )

    callback = Callback(message=_Same())
    api = FakeApi(announcements=[_ann(1)])

    await screens.turn_page(callback, AnnouncementCB(action="page", page=0), api, Role.driver)

    assert callback.answers == [None]
