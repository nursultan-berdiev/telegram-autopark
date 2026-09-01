"""Сквозной прогон апдейта через диспетчер с моком ApiClient.

RoleMiddleware теперь ходит в core-api (GET /me), а не в БД — диспетчер
собирается через app.create_dispatcher(<FakeApi>), см. app/__main__.py.
Регрессия на боевой баг: RoleMiddleware обязана быть OUTER-middleware,
иначе фильтры ролей падают с TypeError на каждом апдейте — сборки роутеров
это не ловят, нужен именно прогон события.

get_main_router() — синглтон роутеров (см. app/handlers/__init__.py): повторно
прикрепить его к другому Dispatcher нельзя ("Router is already attached to
..."). Поэтому диспетчер тут собирается ОДИН раз на модуль поверх общего
FakeApi, а роль/ответы API между тестами меняются мутацией этого FakeApi —
благо RoleMiddleware кэширует роль по user_id, а тестовые роли развязаны
по разным tg id (ADMIN_ID/DRIVER_ID/GUEST_ID).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import SendMessage
from aiogram.types import Chat, Message, Update, User

from tests.conftest import ADMIN_ID, DRIVER_ID, FakeApi

GUEST_ID = 999

ADMIN_ME = {"role": "admin", "driver": None}
DRIVER_ME = {
    "role": "driver",
    "driver": {
        "id": 1,
        "tg_user_id": DRIVER_ID,
        "full_name": "Тест Водитель",
        "car_id": 1,
        "car_plate": "01KG001AAA",
        "active": True,
    },
}
GUEST_ME = {"role": "guest", "driver": None}


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


@pytest.fixture(scope="module")
def shared_api() -> FakeApi:
    return FakeApi()


@pytest.fixture(scope="module")
def dispatcher(shared_api: FakeApi, dispatcher_factory):
    # Через фабрику из conftest: единственный на процесс диспетчер.
    return dispatcher_factory(shared_api)


@pytest.fixture
def bot_with_fake_session() -> Bot:
    return Bot(token="123:TEST", session=FakeSession())


def _message(user_id: int, text: str, update_id: int = 1) -> Update:
    user = User(id=user_id, is_bot=False, first_name="Тест")
    chat = Chat(id=user_id, type="private")
    message = Message(
        message_id=update_id,
        date=datetime.now(timezone.utc),
        chat=chat,
        from_user=user,
        text=text,
    )
    return Update(update_id=update_id, message=message)


async def _feed(dispatcher, bot: Bot, update: Update, api: FakeApi) -> list[str]:
    api.calls.clear()  # изолируем "called(...)" от предыдущих тестов модуля
    await dispatcher.feed_update(bot, update)
    return [c.text for c in bot.session.calls if isinstance(c, SendMessage)]


async def test_start_admin_gets_admin_menu(dispatcher, shared_api, bot_with_fake_session):
    """Апдейт от админа доходит до хендлера (фильтры ролей получают role из /me)."""
    shared_api.set("me", ADMIN_ME)

    texts = await _feed(
        dispatcher, bot_with_fake_session, _message(ADMIN_ID, "/start"), shared_api
    )

    assert texts, "хендлер /start не ответил — апдейт не дошёл"
    assert "администратор" in texts[0].lower()


async def test_start_driver_gets_driver_menu(dispatcher, shared_api, bot_with_fake_session):
    """Водитель получает своё меню, а не админское — роли не должны путаться."""
    shared_api.set("me", DRIVER_ME)

    texts = await _feed(
        dispatcher, bot_with_fake_session, _message(DRIVER_ID, "/start"), shared_api
    )

    assert texts, "хендлер /start не ответил — апдейт не дошёл"
    assert "здравствуйте" in texts[0].lower()
    assert "администратор" not in texts[0].lower()


async def test_start_guest_gets_refusal(dispatcher, shared_api, bot_with_fake_session):
    """Незнакомый пользователь получает нейтральный отказ, а не падение фильтра."""
    shared_api.set("me", GUEST_ME)

    texts = await _feed(
        dispatcher, bot_with_fake_session, _message(GUEST_ID, "/start"), shared_api
    )

    assert texts, "хендлер /start не ответил — апдейт не дошёл"
    assert "нет доступа" in texts[0].lower()


async def test_guest_with_valid_invite_link_gets_registration_step(
    dispatcher, shared_api, bot_with_fake_session
):
    """Гость с рабочей ссылкой видит шаг 1 регистрации — приглашение по ссылке."""
    shared_api.set("me", GUEST_ME)
    shared_api.set(
        "resolve_invitation",
        {"ok": True, "problem": None, "car_id": 1, "car_plate": "01KG001AAA"},
    )

    texts = await _feed(
        dispatcher,
        bot_with_fake_session,
        _message(GUEST_ID, "/start invite-code-1"),
        shared_api,
    )

    assert texts, "хендлер /start <code> не ответил"
    assert "шаг 1" in texts[0].lower()
    assert "01kg001aaa" in texts[0].lower()
    assert shared_api.called("resolve_invitation"), "resolve_invitation не был вызван"


async def test_guest_with_broken_invite_link_gets_problem_text(
    dispatcher, shared_api, bot_with_fake_session
):
    """Просроченная/чужая ссылка — конкретная причина, а не «нет доступа»."""
    shared_api.set("me", GUEST_ME)
    shared_api.set(
        "resolve_invitation",
        {"ok": False, "problem": "expired", "car_id": None, "car_plate": None},
    )

    texts = await _feed(
        dispatcher,
        bot_with_fake_session,
        _message(GUEST_ID, "/start invite-code-2"),
        shared_api,
    )

    assert texts, "хендлер /start <code> не ответил"
    assert "истёк" in texts[0].lower()
