"""Петля drain_outbox: бот шлёт очередь исходящих и помечает доставку."""
from __future__ import annotations

from aiogram.exceptions import TelegramForbiddenError

from app.client import ApiError
from app.scheduler import drain_outbox


class _Bot:
    def __init__(self, *, fail_ids: tuple[int, ...] = (), permanent_ids: tuple[int, ...] = ()) -> None:
        self.sent: list[int] = []
        self.fail_ids = set(fail_ids)
        self.permanent_ids = set(permanent_ids)

    async def send_message(self, chat_id: int, text: str, **kwargs) -> None:
        if chat_id in self.permanent_ids:
            raise TelegramForbiddenError(method=None, message="bot was blocked by the user")
        if chat_id in self.fail_ids:
            raise RuntimeError("временный сбой сети")
        self.sent.append(chat_id)


class _Api:
    def __init__(self, pending, *, fail_fetch: bool = False) -> None:
        self._pending = pending
        self.fail_fetch = fail_fetch
        self.sent: list[int] = []
        self.failed: list[tuple[int, bool]] = []

    async def outbox_pending(self) -> list[dict]:
        if self.fail_fetch:
            raise ApiError("core-api недоступен")
        return self._pending

    async def outbox_sent(self, message_id: int) -> None:
        self.sent.append(message_id)

    async def outbox_failed(self, message_id: int, *, permanent: bool = False) -> None:
        self.failed.append((message_id, permanent))


def _msg(mid: int, recipient: int) -> dict:
    return {"id": mid, "recipient_tg_user_id": recipient, "text": "t", "kind": "reminder"}


async def test_drain_sends_and_marks_sent():
    bot = _Bot()
    api = _Api([_msg(1, 10), _msg(2, 20)])
    out = await drain_outbox(bot, api)
    assert out == 2
    assert bot.sent == [10, 20]
    assert api.sent == [1, 2] and api.failed == []


async def test_drain_transient_error_marks_failed_not_permanent():
    bot = _Bot(fail_ids=(20,))
    api = _Api([_msg(1, 10), _msg(2, 20)])
    out = await drain_outbox(bot, api)
    assert out == 1
    assert bot.sent == [10]
    assert api.sent == [1] and api.failed == [(2, False)]


async def test_drain_permanent_error_marks_failed_permanent():
    bot = _Bot(permanent_ids=(20,))
    api = _Api([_msg(1, 10), _msg(2, 20)])
    out = await drain_outbox(bot, api)
    assert out == 1
    assert bot.sent == [10]
    assert api.failed == [(2, True)]  # заблокирован ботом → сдаёмся сразу


async def test_drain_fetch_error_is_swallowed():
    bot, api = _Bot(), _Api([], fail_fetch=True)
    out = await drain_outbox(bot, api)
    assert out == 0 and bot.sent == [] and api.sent == [] and api.failed == []
