"""Документы: выбор машины админом, свои документы водителя, загрузка, удаление."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import EditMessageText, SendDocument

from app.callbacks import DocCB
from app.client import ApiError
from app.handlers import documents as screens
from app.keyboards.cars import car_detail_kb
from app.keyboards.documents import LABEL_LEN, driver_list_kb
from app.middlewares.role import Role
from app.states.document import DocumentForm
from contracts import DOCUMENT_MAX_FILE_NAME, DOCUMENT_MAX_TITLE
from tests.conftest import ADMIN_ID, DRIVER_ID, FakeApi

DRIVER = {"id": 1, "tg_user_id": DRIVER_ID, "car_id": 7, "car_plate": "01KG007AAA"}


def _doc(doc_id: int, car_id: int | None = 7, **over) -> dict:
    doc = {
        "id": doc_id,
        "car_id": car_id,
        "title": f"Документ {doc_id}",
        "file_id": f"FILE{doc_id}",
        "file_kind": "document",
        "file_name": "osago.pdf",
        "file_size": 245_000,
        "created_by": ADMIN_ID,
    }
    doc.update(over)
    return doc


@dataclass
class Msg:
    answers: list[str] = field(default_factory=list)
    edits: list[str] = field(default_factory=list)
    markups: list[Any] = field(default_factory=list)
    files: list[tuple[str, str, str | None]] = field(default_factory=list)
    text: str | None = None
    document: Any = None
    photo: list | None = None
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

    async def answer_document(self, file_id: str, **kwargs: Any) -> None:
        self.files.append(("document", file_id, kwargs.get("caption")))
        self.markups.append(kwargs.get("reply_markup"))

    async def answer_photo(self, file_id: str, **kwargs: Any) -> None:
        self.files.append(("photo", file_id, kwargs.get("caption")))
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


@dataclass
class TgDocument:
    file_id: str = "BQAC"
    file_name: str | None = "osago.pdf"
    file_size: int | None = 245_000


@dataclass
class TgPhoto:
    file_id: str
    file_size: int | None = 90_000


def _labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row] if markup else []


# --- списки -----------------------------------------------------------------


async def test_admin_picks_common_or_car():
    message = Msg()
    api = FakeApi(cars=[{"id": 7, "plate": "01KG007AAA", "model": "Cobalt"}])

    await screens.open_documents(message, api, Role.admin)

    assert _labels(message.markups[0]) == [
        "🏢 Общие документы парка", "🚗 01KG007AAA · Cobalt",
    ]


async def test_admin_car_list_with_add_button():
    callback = Callback(message=Msg(text=screens.OWNERS_TEXT))
    api = FakeApi(car={"id": 7, "plate": "01KG007AAA"}, car_documents=[_doc(1, title="ОСАГО")])

    await screens.show_list(callback, DocCB(action="list", car_id=7), api)

    assert callback.message.edits == ["🚗 Документы 01KG007AAA: 1"]
    assert _labels(callback.message.markups[0]) == [
        "📄 ОСАГО", "➕ Добавить документ", "⬅️ Назад",
    ]
    assert api.called("common_documents") == []


async def test_list_from_photo_car_card_is_sent_as_new_message():
    """Карточка машины — фото без текста: edit_text на ней упал бы с TelegramBadRequest."""
    callback = Callback(message=Msg(text=None))
    api = FakeApi(car={"id": 7, "plate": "01KG<7>"}, car_documents=[])

    await screens.show_list(callback, DocCB(action="list", car_id=7), api)

    assert callback.message.edits == []
    # Номер вводит админ руками — под глобальным parse_mode=HTML его экранируем.
    assert callback.message.answers == ["🚗 Документы 01KG&lt;7&gt;: документов пока нет."]


async def test_admin_common_list_empty():
    callback = Callback(message=Msg(text=screens.OWNERS_TEXT))
    api = FakeApi(common_documents=[])

    await screens.show_list(callback, DocCB(action="list", car_id=0), api)

    assert callback.message.edits == ["🏢 Общие документы парка: документов пока нет."]
    assert api.called("car") == []


async def test_driver_sees_own_car_and_common_documents():
    message = Msg()
    api = FakeApi(
        car_documents=[_doc(1, title="ОСАГО")],
        common_documents=[_doc(2, car_id=None, title="Договор аренды")],
    )

    await screens.open_documents(message, api, Role.driver, DRIVER)

    ((args, _),) = api.called("car_documents")
    assert args == (7,)  # машина из роли водителя
    assert "01KG007AAA" in message.answers[0]
    assert _labels(message.markups[0]) == ["🚗 ОСАГО", "🏢 Договор аренды"]


async def test_driver_without_car_gets_only_common():
    message = Msg()
    api = FakeApi(common_documents=[])

    await screens.open_documents(message, api, Role.driver, {"id": 1, "car_id": None})

    assert api.called("car_documents") == []
    assert "Документов пока нет" in message.answers[0]


def test_car_card_has_documents_button():
    labels = _labels(car_detail_kb({"id": 7}))
    assert "📄 Документы" in labels


# --- открытие ---------------------------------------------------------------


async def test_driver_opens_own_document_without_admin_buttons():
    callback = Callback(from_user=User(DRIVER_ID))
    api = FakeApi(document=_doc(1, title="ОСАГО <2026>"))

    await screens.open_document(callback, DocCB(action="open", doc_id=1), api, Role.driver, DRIVER)

    assert callback.message.files == [("document", "FILE1", "📄 <b>ОСАГО &lt;2026&gt;</b>")]
    assert callback.message.markups == [None]


async def test_driver_cannot_open_other_car_document():
    callback = Callback(from_user=User(DRIVER_ID))
    api = FakeApi(document=_doc(1, car_id=99))

    await screens.open_document(callback, DocCB(action="open", doc_id=1), api, Role.driver, DRIVER)

    assert callback.message.files == []
    assert callback.answers == ["Документ не найден"]


async def test_driver_without_car_cannot_open_car_document():
    callback = Callback(from_user=User(DRIVER_ID))
    api = FakeApi(document=_doc(1, car_id=7))

    await screens.open_document(
        callback, DocCB(action="open", doc_id=1), api, Role.driver, {"id": 1, "car_id": None}
    )

    assert callback.message.files == []
    assert callback.answers == ["Документ не найден"]


async def test_driver_opens_common_document():
    callback = Callback(from_user=User(DRIVER_ID))
    api = FakeApi(document=_doc(2, car_id=None))

    await screens.open_document(callback, DocCB(action="open", doc_id=2), api, Role.driver, DRIVER)

    assert callback.message.files[0][1] == "FILE2"


async def test_photo_document_is_sent_as_photo_with_delete_for_admin():
    callback = Callback()
    api = FakeApi(document=_doc(3, file_kind="photo"))

    await screens.open_document(callback, DocCB(action="open", doc_id=3), api, Role.admin)

    assert callback.message.files[0][:2] == ("photo", "FILE3")
    assert _labels(callback.message.markups[0]) == ["🗑 Удалить документ"]


async def test_unavailable_file_is_reported():
    class _Gone(Msg):
        async def answer_document(self, file_id: str, **kwargs: Any) -> None:
            raise TelegramBadRequest(
                method=SendDocument(chat_id=1, document=file_id), message="wrong file identifier"
            )

    callback = Callback(message=_Gone(), from_user=User(DRIVER_ID))
    api = FakeApi(document=_doc(1))

    await screens.open_document(callback, DocCB(action="open", doc_id=1), api, Role.driver, DRIVER)

    assert "загрузить заново" in callback.answers[0]


# --- загрузка ---------------------------------------------------------------


async def test_upload_pdf_to_car():
    state = State()
    await screens.add_start(Callback(), DocCB(action="add", car_id=7), state)
    assert state.state == DocumentForm.title

    await screens.add_title(Msg(text="  ОСАГО до 31.12.2026 "), state)
    assert state.state == DocumentForm.file

    api = FakeApi(
        add_document=lambda **kw: {**_doc(5, title=kw["title"])},
        car={"id": 7, "plate": "01KG007AAA"},
        car_documents=[_doc(5, title="ОСАГО до 31.12.2026")],
    )
    message = Msg(document=TgDocument(), from_user=User())
    await screens.add_file(message, state, api)

    ((_, kwargs),) = api.called("add_document")
    assert kwargs == {
        "tg_id": ADMIN_ID,
        "car_id": 7,
        "title": "ОСАГО до 31.12.2026",
        "file_id": "BQAC",
        "file_kind": "document",
        "file_name": "osago.pdf",
        "file_size": 245_000,
    }
    assert state.state is None
    assert message.answers[0] == "✅ Документ «ОСАГО до 31.12.2026» добавлен (239 КБ)."
    assert message.answers[1] == "🚗 Документы 01KG007AAA: 1"


async def test_long_file_name_is_truncated_not_rejected():
    state = State()
    await state.set_state(DocumentForm.file)
    await state.update_data(car_id=7, title="ОСАГО")
    api = FakeApi(add_document=lambda **kw: _doc(5), car={"id": 7, "plate": "A"}, car_documents=[])
    message = Msg(document=TgDocument(file_name="я" * 300 + ".pdf"), from_user=User())

    await screens.add_file(message, state, api)

    ((_, kwargs),) = api.called("add_document")
    assert len(kwargs["file_name"]) == DOCUMENT_MAX_FILE_NAME


async def test_upload_photo_as_common_document():
    state = State()
    await state.set_state(DocumentForm.file)
    await state.update_data(car_id=0, title="Правила парка")
    api = FakeApi(add_document=lambda **kw: _doc(6, car_id=None), common_documents=[])
    message = Msg(photo=[TgPhoto("small"), TgPhoto("big")], from_user=User())

    await screens.add_file(message, state, api)

    ((_, kwargs),) = api.called("add_document")
    assert kwargs["car_id"] is None  # 0 в кнопке — это «общие»
    assert kwargs["file_id"] == "big" and kwargs["file_kind"] == "photo"


async def test_upload_failure_keeps_draft():
    state = State()
    await state.set_state(DocumentForm.file)
    await state.update_data(car_id=7, title="ОСАГО")

    def _boom(**_kw):
        raise ApiError(404, "машина 7 не найдена")

    message = Msg(document=TgDocument(), from_user=User())
    await screens.add_file(message, state, FakeApi(add_document=_boom))

    assert message.answers == ["машина 7 не найдена"]
    assert state.state == DocumentForm.file


async def test_title_length_checked_before_file():
    state = State()
    await state.set_state(DocumentForm.title)
    message = Msg(text="я" * (DOCUMENT_MAX_TITLE + 1))

    await screens.add_title(message, state)

    assert state.state == DocumentForm.title and "от 1 до" in message.answers[0]


def test_file_size_label():
    assert screens.file_size_label(None) == ""
    assert screens.file_size_label(500) == "1 КБ"
    assert screens.file_size_label(3 * 1024 * 1024) == "3.0 МБ"


# --- удаление ---------------------------------------------------------------


async def test_delete_flow():
    callback = Callback()
    await screens.ask_delete(callback, DocCB(action="delete", doc_id=5))
    assert _labels(callback.message.markups[-1]) == ["✅ Да, удалить", "❌ Отмена"]

    await screens.cancel_delete(callback, DocCB(action="delete_cancel", doc_id=5))
    assert _labels(callback.message.markups[-1]) == ["🗑 Удалить документ"]

    api = FakeApi(delete_document=None)
    await screens.delete(callback, DocCB(action="delete_confirm", doc_id=5), api)
    ((args, kwargs),) = api.called("delete_document")
    assert args == (5,) and kwargs == {"tg_id": ADMIN_ID}
    assert callback.message.markups[-1] is None
    assert "удалён" in callback.message.answers[-1]


async def test_double_tap_on_same_list_is_quiet():
    """Повторный тап: тот же текст, Telegram отвечает «not modified» — это не ошибка."""

    class _Same(Msg):
        async def edit_text(self, text: str, **kwargs: Any) -> None:
            raise TelegramBadRequest(
                method=EditMessageText(text=text), message="Bad Request: message is not modified"
            )

    callback = Callback(message=_Same(text=screens.OWNERS_TEXT))
    api = FakeApi(common_documents=[])

    await screens.show_list(callback, DocCB(action="list", car_id=0), api)

    assert callback.answers == [None]


def test_long_title_is_shortened_on_button_only():
    title = "Полис ОСАГО " + "я" * 120
    (label,) = _labels(driver_list_kb([_doc(1, title=title)], []))
    assert label.endswith("…") and len(label) == len("🚗 ") + LABEL_LEN
