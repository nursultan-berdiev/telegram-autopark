"""Сквозной прогон апдейта через диспетчер.

Регрессия на реальный баг боевого запуска: RoleMiddleware был зарегистрирован
как inner-middleware и выполнялся ПОСЛЕ фильтров — фильтры ролей падали с
TypeError ("missing 1 required positional argument: 'role'") на каждом апдейте.
Сборка роутеров этого не ловила, нужен именно прогон события.
"""
from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User

from bot import __main__ as app
from bot.middlewares import role as role_module


class FakeSession(BaseSession):
    """Перехватывает вызовы Telegram API вместо похода в сеть."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list = []

    async def close(self) -> None:  # pragma: no cover - не используется
        pass

    async def make_request(self, bot, method, timeout=None):
        self.calls.append(method)
        return None

    async def stream_content(self, *args, **kwargs):  # pragma: no cover
        yield b""


def _start_update(user_id: int) -> Update:
    user = User(id=user_id, is_bot=False, first_name="Тест")
    chat = Chat(id=user_id, type="private")
    message = Message(
        message_id=1,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=user,
        text="/start",
    )
    return Update(update_id=1, message=message)


@pytest.fixture
def bot_with_fake_session() -> Bot:
    return Bot(token="123:TEST", session=FakeSession())


async def _feed(dispatcher, bot: Bot, user_id: int) -> list:
    await dispatcher.feed_update(bot, _start_update(user_id))
    return [c for c in bot.session.calls if isinstance(c, SendMessage)]


async def test_start_admin_gets_menu(
    dispatcher, bot_with_fake_session, session_maker, monkeypatch
):
    """Апдейт от админа доходит до хендлера (фильтры получают role)."""
    monkeypatch.setattr(role_module, "async_session_maker", session_maker)
    # 111 — админ из ADMIN_IDS в conftest
    sent = await _feed(dispatcher, bot_with_fake_session, 111)

    assert sent, "хендлер /start не ответил — апдейт не дошёл"
    assert "администратор" in sent[0].text.lower()


async def test_start_guest_gets_refusal(
    dispatcher, bot_with_fake_session, session_maker, monkeypatch
):
    """Незнакомый пользователь получает нейтральный отказ, а не падение фильтра."""
    monkeypatch.setattr(role_module, "async_session_maker", session_maker)
    sent = await _feed(dispatcher, bot_with_fake_session, 999)

    assert sent, "хендлер /start не ответил — апдейт не дошёл"
    assert "нет доступа" in sent[0].text.lower()
