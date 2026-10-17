"""Фотоконтроль в боте: сдача водителем по шагам, уведомление админам, просмотр."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText

from app.callbacks import PhotoCB
from app.client import ApiError
from app.handlers import photo as screens
from app.keyboards.photo import due_label, status_mark
from app.middlewares.role import Role
from app.states.photo import PhotoForm
from tests.conftest import ADMIN_ID, DRIVER_ID, FakeApi

TODAY = date(2026, 10, 17)
DRIVER = {"id": 1, "tg_user_id": DRIVER_ID, "car_id": 7, "car_plate": "01KG007AAA"}
FILES = {
    "front_file_id": "F",
    "rear_file_id": "R",
    "interior_front_file_id": "IF",
    "interior_rear_file_id": "IR",
}


def _status(**over) -> dict:
    status = {
        "driver_id": 1,
        "driver_name": "Иванов",
        "car_id": 7,
        "car_plate": "01KG007AAA",
        "next_due_on": "2026-10-24",
        "overdue_days": 0,
        "last_inspection_id": None,
        "last_inspection_at": None,
    }
    status.update(over)
    return status


def _inspection(**over) -> dict:
    item = {
        "id": 9,
        "driver_id": 1,
        "driver_name": "Иванов",
        "car_id": 7,
        "car_plate": "01KG007AAA",
        **FILES,
        "has_issues": False,
        "comment": None,
        "created_at": "2026-10-17T06:00:00+00:00",
    }
    item.update(over)
    return item


@dataclass
class Photo:
    file_id: str
    file_unique_id: str = ""

    def __post_init__(self) -> None:
        self.file_unique_id = self.file_unique_id or f"u-{self.file_id}"


@dataclass
class Msg:
    answers: list[str] = field(default_factory=list)
    edits: list[str] = field(default_factory=list)
    markups: list[Any] = field(default_factory=list)
    albums: list[list] = field(default_factory=list)
    text: str | None = None
    photo: list | None = None
    from_user: Any = None

    async def answer(self, text: str, **kwargs: Any) -> "Msg":
        self.answers.append(text)
        self.markups.append(kwargs.get("reply_markup"))
        return self

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.edits.append(text)
        self.markups.append(kwargs.get("reply_markup"))

    async def answer_media_group(self, media: list, **kwargs: Any) -> None:
        self.albums.append(media)


@dataclass
class User:
    id: int = DRIVER_ID


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

    async def get_state(self):
        return self.state

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def get_data(self) -> dict:
        return dict(self.data)

    async def clear(self) -> None:
        self.state, self.data = None, {}


class Bot:
    def __init__(self) -> None:
        self.albums: list[tuple[int, list]] = []
        self.messages: list[tuple[int, str]] = []

    async def send_media_group(self, chat_id: int, media: list) -> None:
        self.albums.append((chat_id, media))

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.messages.append((chat_id, text))


def _labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row] if markup else []


# --- статус -----------------------------------------------------------------


def test_status_labels():
    assert due_label(_status(overdue_days=2), TODAY) == "просрочен 2 дн."
    assert due_label(_status(next_due_on="2026-10-17"), TODAY) == "срок сегодня"
    assert due_label(_status(), TODAY) == "до 24.10"
    assert status_mark(_status(overdue_days=1), TODAY) == "🔴"
    assert status_mark(_status(next_due_on="2026-10-17"), TODAY) == "🟡"
    assert status_mark(_status(), TODAY) == "🟢"


def test_driver_status_text_variants():
    assert "Следующий — до 24.10.2026" in screens.driver_status_text(_status(), TODAY)
    overdue = screens.driver_status_text(_status(overdue_days=3), TODAY)
    assert "Просрочен на 3 дн." in overdue
    last = screens.driver_status_text(
        _status(last_inspection_at="2026-10-10T06:00:00+00:00"), TODAY
    )
    assert "Последний пройден: 10.10.2026 12:00" in last


async def test_driver_sees_own_status_with_start_button():
    message = Msg()
    api = FakeApi(driver_photo_status=_status())

    await screens.open_photo(message, api, Role.driver, DRIVER)

    ((args, _),) = api.called("driver_photo_status")
    assert args == (1,)  # водитель из роли
    assert "01KG007AAA" in message.answers[0]
    assert _labels(message.markups[0]) == ["📸 Пройти фотоконтроль"]


async def test_driver_without_car_is_told_so():
    def _missing(*_a, **_k):
        raise ApiError(404, "нет работающего водителя с машиной")

    message = Msg()
    await screens.open_photo(message, FakeApi(driver_photo_status=_missing), Role.driver, DRIVER)

    assert "не закреплена машина" in message.answers[0]


async def test_admin_sees_fleet_list():
    message = Msg()
    api = FakeApi(photo_statuses=[_status(overdue_days=2), _status(driver_id=2, car_plate="B")])

    await screens.open_photo(message, api, Role.admin)

    assert message.answers == ["📸 Фотоконтроль: водителей 2, просрочено 1."]
    assert _labels(message.markups[0])[0] == "🔴 01KG007AAA · Иванов — просрочен 2 дн."


# --- сдача ------------------------------------------------------------------


async def _walk_photos(state: State) -> Msg:
    await screens.start(Callback(), state)
    last = Msg()
    for file_id in FILES.values():
        last = Msg(photo=[Photo("small"), Photo(file_id)])
        await screens.take_photo(last, state)
    return last


async def test_four_photos_then_report_question():
    state = State()
    last = await _walk_photos(state)

    assert state.state == PhotoForm.report
    # Берём самое крупное фото.
    assert {k: v for k, v in state.data.items() if k in FILES} == FILES
    assert _labels(last.markups[0]) == ["✅ Всё в порядке", "⚠️ Есть замечания"]


async def test_each_step_asks_next_angle():
    state = State()
    await screens.start(Callback(), state)
    message = Msg(photo=[Photo("F")])

    await screens.take_photo(message, state)

    assert state.state == PhotoForm.rear and "Шаг 2 из 4" in message.answers[0]


async def test_non_photo_is_rejected():
    message = Msg()

    await screens.take_photo_invalid(message)

    assert "Нужна фотография" in message.answers[0]


async def test_report_ok_submits_and_notifies_admins():
    state = State()
    await _walk_photos(state)
    api = FakeApi(
        submit_photo_inspection={"inspection": _inspection(), "next_due_on": "2026-10-24"}
    )
    bot = Bot()
    callback = Callback()

    await screens.report_ok(callback, state, api, bot)

    ((_, kwargs),) = api.called("submit_photo_inspection")
    assert kwargs == {"tg_id": DRIVER_ID, "has_issues": False, "comment": None, **FILES}
    assert state.state is None
    assert "Следующий — до 24.10.2026" in callback.message.answers[0]
    chats = [chat for chat, _ in bot.albums]
    assert chats == [ADMIN_ID, 222]
    media = bot.albums[0][1]
    assert [m.media for m in media] == ["F", "R", "IF", "IR"]
    assert [m.caption for m in media] == ["Спереди", "Сзади", "Салон спереди", "Салон сзади"]
    assert "✅ Замечаний нет" in bot.messages[0][1]


async def test_issues_need_description():
    state = State()
    await _walk_photos(state)
    await screens.report_issues(Callback(), state)
    assert state.state == PhotoForm.comment

    api = FakeApi(
        submit_photo_inspection={
            "inspection": _inspection(has_issues=True, comment="скол <бампер>"),
            "next_due_on": "2026-10-24",
        }
    )
    bot = Bot()
    await screens.take_comment(Msg(text="  скол <бампер> ", from_user=User()), state, api, bot)

    ((_, kwargs),) = api.called("submit_photo_inspection")
    assert kwargs["has_issues"] is True and kwargs["comment"] == "скол <бампер>"
    assert "⚠️ Есть замечания: скол &lt;бампер&gt;" in bot.messages[0][1]


async def test_submit_failure_keeps_photos():
    state = State()
    await _walk_photos(state)

    def _down(**_kw):
        raise ApiError(503, "Сервер недоступен, попробуйте позже.")

    callback = Callback()
    await screens.report_ok(callback, state, FakeApi(submit_photo_inspection=_down), Bot())

    assert state.state == PhotoForm.report
    assert {k: v for k, v in state.data.items() if k in FILES} == FILES
    assert "Не удалось сдать" in callback.message.answers[0]
    assert _labels(callback.message.markups[0]) == ["✅ Всё в порядке", "⚠️ Есть замечания"]


async def test_admin_notification_failure_does_not_break_submission():
    class _Down(Bot):
        async def send_media_group(self, chat_id: int, media: list) -> None:
            raise RuntimeError("telegram down")

    state = State()
    await _walk_photos(state)
    api = FakeApi(
        submit_photo_inspection={"inspection": _inspection(), "next_due_on": "2026-10-24"}
    )
    callback = Callback()

    await screens.report_ok(callback, state, api, _Down())

    assert "принят" in callback.message.answers[0]


# --- просмотр админом -------------------------------------------------------


async def test_admin_opens_driver_history_and_album():
    callback = Callback(from_user=User(ADMIN_ID))
    api = FakeApi(photo_inspections=[_inspection(), _inspection(id=8, has_issues=True)])

    await screens.show_driver(callback, PhotoCB(action="driver", driver_id=1), api)

    assert callback.message.edits[0].startswith("📸 01KG007AAA · Иванов")
    labels = _labels(callback.message.markups[0])
    assert labels == [
        "✅ 17.10.2026 12:00",
        "⚠️ 17.10.2026 12:00 · есть замечания",
        "⬅️ К списку водителей",
    ]

    api = FakeApi(photo_inspection=_inspection())
    opened = Callback(from_user=User(ADMIN_ID))
    await screens.open_inspection(opened, PhotoCB(action="open", insp_id=9), api)
    assert [m.media for m in opened.message.albums[0]] == ["F", "R", "IF", "IR"]
    assert "Водитель: Иванов" in opened.message.answers[0]


async def test_report_for_deleted_car():
    text = screens.report_text(_inspection(car_plate=None))
    assert "машина удалена" in text


async def test_step_detected_from_storage_string_state():
    """Настоящее хранилище FSM отдаёт состояние строкой «PhotoForm:rear», а не объектом."""
    state = State()
    await state.set_state(PhotoForm.rear.state)
    await state.update_data(front_file_id="F")

    await screens.take_photo(Msg(photo=[Photo("R")]), state)

    assert state.state == PhotoForm.interior_front and state.data["rear_file_id"] == "R"


async def test_double_tap_on_same_screen_is_quiet():
    """Повторный тап: тот же текст, Telegram отвечает «not modified» — это не ошибка."""

    class _Same(Msg):
        async def edit_text(self, text: str, **kwargs: Any) -> None:
            raise TelegramBadRequest(
                method=EditMessageText(text=text), message="Bad Request: message is not modified"
            )

    callback = Callback(message=_Same(), from_user=User(ADMIN_ID))

    await screens.show_drivers(callback, FakeApi(photo_statuses=[_status()]))

    assert callback.answers == [None]


async def test_comment_must_be_non_empty_and_bounded():
    from contracts import PHOTO_COMMENT_MAX

    for text in ("   ", "я" * (PHOTO_COMMENT_MAX + 1)):
        state = State()
        await state.set_state(PhotoForm.comment)
        api = FakeApi()
        message = Msg(text=text, from_user=User())

        await screens.take_comment(message, state, api, Bot())

        assert api.called("submit_photo_inspection") == []
        assert state.state == PhotoForm.comment
        assert "Нужно описание" in message.answers[0]


async def test_same_photo_twice_is_rejected_at_the_step():
    state = State()
    await screens.start(Callback(), state)
    await screens.take_photo(Msg(photo=[Photo("F", "same")]), state)
    message = Msg(photo=[Photo("F-forwarded", "same")])

    await screens.take_photo(message, state)

    assert state.state == PhotoForm.rear
    assert "уже было" in message.answers[0]
    assert "rear_file_id" not in state.data
