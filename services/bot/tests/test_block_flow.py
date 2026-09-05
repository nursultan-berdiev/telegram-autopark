"""Поток блокировки из карточки алерта: команда, отказ гейта, уведомление водителя."""
from dataclasses import dataclass, field
from typing import Any

import pytest

from app.callbacks import AlertCB
from app.handlers import alerts as alerts_handlers
from tests.conftest import ADMIN_ID, FakeApi


@dataclass
class FakeBot:
    sent: list[tuple[int, str]] = field(default_factory=list)

    async def send_message(self, chat_id: int, text: str, **kwargs: Any) -> None:
        self.sent.append((chat_id, text))


@dataclass
class FakeMessage:
    bot: FakeBot
    answers: list[str] = field(default_factory=list)
    markup_cleared: bool = False

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)

    async def edit_reply_markup(self, reply_markup=None) -> None:
        self.markup_cleared = True


@dataclass
class FakeUser:
    id: int = ADMIN_ID


@dataclass
class FakeCallback:
    message: FakeMessage
    bot: FakeBot
    from_user: FakeUser = field(default_factory=FakeUser)
    answered: bool = False

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        self.answered = True


@pytest.fixture
def callback() -> FakeCallback:
    bot = FakeBot()
    return FakeCallback(message=FakeMessage(bot=bot), bot=bot)


def _api_with_driver(**over: Any) -> FakeApi:
    api = FakeApi(
        car={"id": 3, "plate": "01KG777AAA", "driver_id": 5},
        driver={"driver": {"id": 5, "tg_user_id": 4242, "full_name": "Водитель"}},
    )
    for key, value in over.items():
        api.set(key, value)
    return api


async def test_block_sends_command_and_notifies_driver(callback):
    api = _api_with_driver(
        command={"ok": True, "command": {"status": "sent"}, "reason": None}
    )

    await alerts_handlers.block_engine(
        callback, AlertCB(action="block", alert_id=7, car_id=3), api
    )

    assert api.called("command")[0][1]["type"] == "engine_block"
    assert callback.message.markup_cleared, "кнопку убираем до запроса — против двойного тапа"
    assert any("блокировк" in a.lower() for a in callback.message.answers)
    assert callback.bot.sent and callback.bot.sent[0][0] == 4242
    assert "01KG777AAA" in callback.bot.sent[0][1]


async def test_gate_refusal_is_explained_and_driver_not_notified(callback):
    api = _api_with_driver(
        command={
            "ok": False,
            "command": {"status": "blocked_by_safety"},
            "reason": "машина в движении",
        }
    )

    await alerts_handlers.block_engine(
        callback, AlertCB(action="block", alert_id=7, car_id=3), api
    )

    assert any("машина в движении" in a for a in callback.message.answers)
    assert callback.bot.sent == [], "машину не заблокировали — водителю писать не о чем"


async def test_unblock_notifies_driver(callback):
    api = _api_with_driver(command={"ok": True, "command": {"status": "sent"}})

    await alerts_handlers.unblock_engine(
        callback, AlertCB(action="unblock", alert_id=7, car_id=3), api
    )

    assert api.called("command")[0][1]["type"] == "engine_unblock"
    assert "разблокирован" in callback.bot.sent[0][1]


async def test_ack_marks_alert(callback):
    api = _api_with_driver(ack_alert={"status": "acknowledged"})

    await alerts_handlers.ack_alert(
        callback, AlertCB(action="ack", alert_id=7, car_id=3), api
    )

    assert api.called("ack_alert") == [((7,), {})]
    assert callback.message.markup_cleared


async def test_maintenance_done_resets_base(callback):
    api = _api_with_driver(maintenance_done={"id": 1})

    await alerts_handlers.maintenance_done(
        callback, AlertCB(action="maint_done", alert_id=7, car_id=3), api
    )

    assert api.called("maintenance_done")[0][0] == (3, "oil")
    assert any("пробега" in a for a in callback.message.answers)
