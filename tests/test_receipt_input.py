"""Чек приходит не только фотографией.

Регрессия по итогам QA (блоки 5 и 6): бот ловил только F.photo, поэтому скриншот
чека, отправленный файлом, и PDF-выписка из банка отбивались сообщением «Нужна
фотография чека» — распознавание до ИИ вообще не доходило.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from aiogram import Bot
from aiogram.methods import SendDocument, SendMessage, SendPhoto
from aiogram.types import Chat, Document, Message, PhotoSize, Update, User

from bot import __main__ as app
from bot.handlers import payments as payments_handler
from bot.middlewares import role as role_module
from bot.services import ai as ai_service
from bot.services import cars as cars_service
from bot.services import drivers as drivers_service
from tests.test_dispatch import FakeSession

DRIVER_TG_ID = 555


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


# ----------------------------------------------------------- сквозной прогон
def _document_update(mime: str, file_name: str, size: int = 1024) -> Update:
    user = User(id=DRIVER_TG_ID, is_bot=False, first_name="Водитель")
    message = Message(
        message_id=2,
        date=datetime.now(timezone.utc),
        chat=Chat(id=DRIVER_TG_ID, type="private"),
        from_user=user,
        document=Document(
            file_id="doc-1", file_unique_id="u1", mime_type=mime,
            file_name=file_name, file_size=size,
        ),
    )
    return Update(update_id=2, message=message)


def _photo_update() -> Update:
    user = User(id=DRIVER_TG_ID, is_bot=False, first_name="Водитель")
    message = Message(
        message_id=3,
        date=datetime.now(timezone.utc),
        chat=Chat(id=DRIVER_TG_ID, type="private"),
        from_user=user,
        photo=[PhotoSize(
            file_id="ph-1", file_unique_id="u2", width=800, height=600, file_size=2048
        )],
    )
    return Update(update_id=3, message=message)


@pytest.fixture
async def driver_stand(session_maker, monkeypatch):
    """Водитель в БД + подменённые сеть, ИИ и хранилище."""
    monkeypatch.setattr(role_module, "async_session_maker", session_maker)
    async with session_maker() as s:
        car = await cars_service.create_car(
            s, plate="QA-777", model=None, photo_file_id=None, photo_path=None
        )
        await drivers_service.register_driver(
            s, tg_user_id=DRIVER_TG_ID, full_name="Тест Водитель", phone="+996",
            inn="12345678", selfie_file_id="s", selfie_path="p", car_id=car.id,
        )

    seen: dict = {}

    async def fake_download(bot, file_id, *, media_type=None):
        seen["media_type"] = media_type
        return b"receipt-bytes", media_type or "image/jpeg"

    async def fake_save(bot, file_id, subdir, name):
        return f"/files/{subdir}/{name}.bin"

    async def fake_recognize(image_bytes, media_type):
        seen["recognized_as"] = media_type
        return ai_service.RecognizedReceipt(
            readable=True, amount=1500.0, currency="KGS",
            paid_at=datetime(2026, 7, 14, 12, 0), paid_at_raw="14.07.2026 12:00",
            note=None,
        )

    monkeypatch.setattr(payments_handler, "download_file_bytes", fake_download)
    monkeypatch.setattr(payments_handler, "save_telegram_file", fake_save)
    monkeypatch.setattr(payments_handler.ai_service, "recognize_receipt", fake_recognize)
    return seen


async def _pay_then(dispatcher, bot: Bot, update: Update) -> list[str]:
    """Водитель жмёт «Оплатить», затем присылает файл. Возвращает тексты ответов."""
    from bot.keyboards.driver_menu import BTN_PAY

    start = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=Chat(id=DRIVER_TG_ID, type="private"),
        from_user=User(id=DRIVER_TG_ID, is_bot=False, first_name="Водитель"),
        text=BTN_PAY,
    )
    await dispatcher.feed_update(bot, Update(update_id=1, message=start))
    await dispatcher.feed_update(bot, update)
    return [c.text for c in bot.session.calls if isinstance(c, SendMessage)]


async def test_pdf_receipt_reaches_ai(dispatcher, driver_stand):
    """PDF-выписка из банка должна дойти до распознавания как PDF."""
    bot = Bot(token="123:TEST", session=FakeSession())

    texts = await _pay_then(dispatcher, bot, _document_update("application/pdf", "chek.pdf"))

    assert driver_stand.get("recognized_as") == "application/pdf", (
        "PDF не дошёл до ИИ — бот снова принимает только фото"
    )
    assert any("1500" in t for t in texts), "распознанная сумма не показана"


async def test_png_screenshot_as_file_reaches_ai(dispatcher, driver_stand):
    """Скриншот, отправленный файлом (а не фото), тоже принимается."""
    bot = Bot(token="123:TEST", session=FakeSession())

    await _pay_then(dispatcher, bot, _document_update("image/png", "screen.png"))

    assert driver_stand.get("recognized_as") == "image/png"


async def test_photo_still_works(dispatcher, driver_stand):
    """Старый путь (фотография) не сломан."""
    bot = Bot(token="123:TEST", session=FakeSession())

    await _pay_then(dispatcher, bot, _photo_update())

    assert driver_stand.get("recognized_as") == "image/jpeg"


async def test_video_rejected_before_ai(dispatcher, driver_stand):
    """Неподходящий файл отсекается до обращения к ИИ."""
    bot = Bot(token="123:TEST", session=FakeSession())

    texts = await _pay_then(dispatcher, bot, _document_update("video/mp4", "clip.mp4"))

    assert "recognized_as" not in driver_stand, "ИИ не должен вызываться на видео"
    assert any("не подходит" in t.lower() for t in texts)


async def test_oversized_file_rejected(dispatcher, driver_stand):
    """Слишком большой файл не тащим в память и в ИИ."""
    bot = Bot(token="123:TEST", session=FakeSession())
    huge = payments_handler.MAX_RECEIPT_BYTES + 1

    texts = await _pay_then(
        dispatcher, bot, _document_update("application/pdf", "big.pdf", size=huge)
    )

    assert "recognized_as" not in driver_stand
    assert any("слишком большой" in t.lower() for t in texts)


# --------------------------------------------------- пересылка чека владельцу
async def test_pdf_receipt_sent_to_owner_as_document():
    """PDF нельзя слать владельцу как фото — Telegram отвергнет file_id."""
    bot = Bot(token="123:TEST", session=FakeSession())
    payment = SimpleNamespace(receipt_file_id="doc-1", receipt_kind="document")

    await payments_handler._send_receipt(bot, 111, payment)

    assert any(isinstance(c, SendDocument) for c in bot.session.calls)
    assert not any(isinstance(c, SendPhoto) for c in bot.session.calls)


async def test_photo_receipt_sent_to_owner_as_photo():
    bot = Bot(token="123:TEST", session=FakeSession())
    payment = SimpleNamespace(receipt_file_id="ph-1", receipt_kind="photo")

    await payments_handler._send_receipt(bot, 111, payment)

    assert any(isinstance(c, SendPhoto) for c in bot.session.calls)
