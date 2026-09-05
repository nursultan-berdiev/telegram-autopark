"""Чек приходит не только фотографией — и не любой файл годится в чек.

Регрессия по итогам QA (блоки 5 и 6): бот когда-то ловил только F.photo, поэтому
скриншот чека, отправленный файлом, и PDF-выписка из банка отбивались без
похода в ИИ. Тип/размер файла бот проверяет ДО обращения к core-api — здесь и
проверяем: на видео/txt/oversize `api.recognize_receipt` вызываться не должен,
на photo/PDF/скриншот-документ — должен.

Хендлеры вызываются напрямую (без Dispatcher/Router): `get_main_router()`
(app/handlers/__init__.py) кэширует роутер-синглтон, который можно прикрепить
к Dispatcher только один раз за процесс. tests/test_dispatch.py уже занимает
этот единственный слот своим локальным module-fixture (`create_dispatcher`
напрямую, в обход conftest.dispatcher_factory) — из-за порядка коллекции
файлов (test_dispatch < test_receipt_input) любой второй create_dispatcher()
в процессе падает с "Router is already attached", и это ломает весь файл
разом. Прямой вызов хендлера с замоканными Message/CallbackQuery/FSMContext/
ApiClient не задействует Dispatcher вовсе — устойчив к этой гонке и к порядку
запуска тестов.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.methods import SendDocument, SendMessage, SendPhoto
from aiogram.types import CallbackQuery, Chat, Document, File, Message, PhotoSize, User

from app.callbacks import PaymentCB
from app.client import ApiError
from app.handlers import payments as payments_handler
from app.states.payment import PaymentFlow
from tests.conftest import DRIVER_ID, FakeApi


class FakeSession(BaseSession):
    """Перехватывает вызовы Telegram API, включая скачивание файла чека."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []

    async def close(self) -> None:  # pragma: no cover - не используется
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        if method.__class__.__name__ == "GetFile":
            return File(file_id=method.file_id, file_unique_id="u", file_path="f.bin")
        return None

    async def stream_content(self, url, **kwargs):
        yield b"fake-receipt-bytes"


def _bot() -> Bot:
    return Bot(token="123:TEST", session=FakeSession())


def _state() -> FSMContext:
    return FSMContext(
        storage=MemoryStorage(),
        key=StorageKey(bot_id=1, chat_id=DRIVER_ID, user_id=DRIVER_ID),
    )


def _driver() -> dict:
    return {
        "id": 1,
        "tg_user_id": DRIVER_ID,
        "full_name": "Тест Водитель",
        "car_id": 1,
        "car_plate": "01KG001AAA",
        "active": True,
    }


def _message(bot: Bot, **kwargs) -> Message:
    user = User(id=DRIVER_ID, is_bot=False, first_name="Водитель")
    msg = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=DRIVER_ID, type="private"),
        from_user=user,
        **kwargs,
    )
    return msg.as_(bot)


def _sent_texts(bot: Bot) -> list[str]:
    return [c.text for c in bot.session.calls if isinstance(c, SendMessage)]


# ------------------------------------------------------- какой файл вообще чек
@pytest.mark.parametrize(
    "mime, file_name, expected",
    [
        ("application/pdf", "receipt.pdf", "application/pdf"),
        ("image/png", "screenshot.png", "image/png"),
        ("image/jpeg", "photo.jpg", "image/jpeg"),
        # Telegram иногда отдаёт PDF как octet-stream — спасает имя файла.
        ("application/octet-stream", "vypiska.PDF", "application/pdf"),
        ("video/mp4", "clip.mp4", None),
        ("", "notes.txt", None),
    ],
)
def test_document_media_type(mime, file_name, expected):
    doc = SimpleNamespace(mime_type=mime, file_name=file_name)
    assert payments_handler._document_media_type(doc) == expected


# ----------------------------------------------------------- приём/отклонение
async def test_pdf_receipt_reaches_api(driver_api: FakeApi):
    """PDF-выписка из банка должна дойти до распознавания через core-api."""
    driver_api.set(
        "recognize_receipt", {"amount": 1500.0, "currency": "KGS", "paid_at": None}
    )
    bot = _bot()
    message = _message(
        bot,
        document=Document(
            file_id="doc-1", file_unique_id="u1", mime_type="application/pdf",
            file_name="chek.pdf", file_size=1024,
        ),
    )

    await payments_handler.pay_receipt(
        message=message, state=_state(), api=driver_api, driver=_driver(), bot=bot
    )

    calls = driver_api.called("recognize_receipt")
    assert calls, "PDF не дошёл до распознавания — бот снова принимает только фото"
    assert calls[0][0][1] == "application/pdf"
    assert any("1500" in t for t in _sent_texts(bot)), "распознанная сумма не показана"


async def test_png_screenshot_as_file_reaches_api(driver_api: FakeApi):
    """Скриншот, отправленный файлом (а не фото), тоже принимается."""
    driver_api.set(
        "recognize_receipt", {"amount": 500.0, "currency": None, "paid_at": None}
    )
    bot = _bot()
    message = _message(
        bot,
        document=Document(
            file_id="doc-1", file_unique_id="u1", mime_type="image/png",
            file_name="screen.png", file_size=1024,
        ),
    )

    await payments_handler.pay_receipt(
        message=message, state=_state(), api=driver_api, driver=_driver(), bot=bot
    )

    calls = driver_api.called("recognize_receipt")
    assert calls and calls[0][0][1] == "image/png"


async def test_photo_still_works(driver_api: FakeApi):
    """Старый путь (фотография) не сломан."""
    driver_api.set(
        "recognize_receipt", {"amount": 100.0, "currency": None, "paid_at": None}
    )
    bot = _bot()
    message = _message(
        bot,
        photo=[PhotoSize(
            file_id="ph-1", file_unique_id="u2", width=800, height=600, file_size=2048
        )],
    )

    await payments_handler.pay_receipt(
        message=message, state=_state(), api=driver_api, driver=_driver(), bot=bot
    )

    calls = driver_api.called("recognize_receipt")
    assert calls and calls[0][0][1] == "image/jpeg"


async def test_video_rejected_before_api(driver_api: FakeApi):
    """Неподходящий файл отсекается до обращения к core-api."""
    bot = _bot()
    message = _message(
        bot,
        document=Document(
            file_id="doc-1", file_unique_id="u1", mime_type="video/mp4",
            file_name="clip.mp4", file_size=1024,
        ),
    )

    await payments_handler.pay_receipt(
        message=message, state=_state(), api=driver_api, driver=_driver(), bot=bot
    )

    assert driver_api.called("recognize_receipt") == [], "API не должен вызываться на видео"
    assert any("не подходит" in t.lower() for t in _sent_texts(bot))


async def test_oversized_file_rejected(driver_api: FakeApi):
    """Слишком большой файл не тащим в API."""
    bot = _bot()
    huge = payments_handler.MAX_RECEIPT_BYTES + 1
    message = _message(
        bot,
        document=Document(
            file_id="doc-1", file_unique_id="u1", mime_type="application/pdf",
            file_name="big.pdf", file_size=huge,
        ),
    )

    await payments_handler.pay_receipt(
        message=message, state=_state(), api=driver_api, driver=_driver(), bot=bot
    )

    assert driver_api.called("recognize_receipt") == []
    assert any("слишком большой" in t.lower() for t in _sent_texts(bot))


async def test_unrecognized_receipt_not_confirmed(driver_api: FakeApi):
    """core-api не смог распознать сумму — бот просит отправить чек ещё раз."""
    driver_api.set("recognize_receipt", {"amount": None, "currency": None, "paid_at": None})
    bot = _bot()
    message = _message(
        bot,
        photo=[PhotoSize(file_id="ph-1", file_unique_id="u2", width=1, height=1)],
    )

    await payments_handler.pay_receipt(
        message=message, state=_state(), api=driver_api, driver=_driver(), bot=bot
    )

    assert any("не удалось распознать" in t.lower() for t in _sent_texts(bot))


async def test_duplicate_receipt_shown_on_confirm(driver_api: FakeApi):
    """Дубль чека возвращается сервером как 409 при create_payment (confirm)."""

    def _conflict(**kwargs):
        raise ApiError(409, "чек уже был")

    driver_api.set("create_payment", _conflict)
    bot = _bot()
    state = _state()
    await state.update_data(
        file_id="ph-1",
        receipt_kind="photo",
        rhash="hash1",
        recognized={"amount": 1500.0, "currency": "KGS", "paid_at": None},
    )
    await state.set_state(PaymentFlow.confirm)

    message = _message(
        bot,
        photo=[PhotoSize(file_id="ph-1", file_unique_id="u2", width=1, height=1)],
    )
    query = CallbackQuery(
        id="q1",
        from_user=message.from_user,
        chat_instance="ci",
        message=message,
        data=PaymentCB(action="confirm").pack(),
    ).as_(bot)

    await payments_handler.pay_confirm(
        query=query, state=state, api=driver_api, driver=_driver(), bot=bot
    )

    assert any("уже был принят" in t for t in _sent_texts(bot))


# --------------------------------------------------- пересылка чека владельцу
async def test_pdf_receipt_sent_to_owner_as_document():
    """PDF нельзя слать владельцу как фото — Telegram отвергнет file_id."""
    bot = _bot()
    payment = {"receipt_file_id": "doc-1", "receipt_kind": "document"}

    await payments_handler._send_receipt(bot, 111, payment)

    assert any(isinstance(c, SendDocument) for c in bot.session.calls)
    assert not any(isinstance(c, SendPhoto) for c in bot.session.calls)


async def test_photo_receipt_sent_to_owner_as_photo():
    bot = _bot()
    payment = {"receipt_file_id": "ph-1", "receipt_kind": "photo"}

    await payments_handler._send_receipt(bot, 111, payment)

    assert any(isinstance(c, SendPhoto) for c in bot.session.calls)
