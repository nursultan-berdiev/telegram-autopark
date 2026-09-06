"""Доставка алертов: повтор до тех пор, пока хоть кто-то их не увидит.

Раньше алерт помечался доставленным независимо от исхода отправки: одна
ошибка Telegram — и уведомление исчезало навсегда, потому что следующий
проход его пропускал.
"""
from __future__ import annotations

import pytest

import app.alerts as alerts_module
from app.alerts import poll_alerts


class _Bot:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[int] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        if self.fail:
            raise RuntimeError("Forbidden: bot was blocked by the user")
        self.sent.append(chat_id)


class _Api:
    def __init__(self, alerts: list[dict]) -> None:
        self._alerts = alerts

    async def alerts(self, status: str = "open") -> list[dict]:
        return self._alerts

    async def fines(self, car_id: int, **kwargs) -> list[dict]:
        return []


def _alert(alert_id: int = 1) -> dict:
    return {
        "id": alert_id,
        "car_id": 3,
        "car_plate": "01KG777AAA",
        "type": "overdue_payment",
        "severity": "warning",
        "text": "просрочка",
    }


@pytest.fixture(autouse=True)
def _clean_state():
    alerts_module._delivered.clear()
    yield
    alerts_module._delivered.clear()


async def test_delivered_alert_is_not_repeated(monkeypatch):
    monkeypatch.setattr(alerts_module.settings, "admin_ids", [1, 2])
    bot, api = _Bot(), _Api([_alert()])

    assert await poll_alerts(bot, api) == 2
    assert await poll_alerts(bot, api) == 0, "второй раз то же самое не шлём"


async def test_undelivered_alert_is_retried(monkeypatch):
    """Ни один админ не получил — значит алерт ещё не показан никому."""
    monkeypatch.setattr(alerts_module.settings, "admin_ids", [1])
    api = _Api([_alert()])

    assert await poll_alerts(_Bot(fail=True), api) == 0

    working = _Bot()
    assert await poll_alerts(working, api) == 1
    assert working.sent == [1]


async def test_partial_delivery_counts_as_delivered(monkeypatch):
    """Один админ заблокировал бота — остальные уведомление получили."""
    monkeypatch.setattr(alerts_module.settings, "admin_ids", [1, 2])

    class _Picky(_Bot):
        async def send_message(self, chat_id, text, reply_markup=None):
            if chat_id == 1:
                raise RuntimeError("Forbidden: bot was blocked by the user")
            self.sent.append(chat_id)

    bot, api = _Picky(), _Api([_alert()])

    assert await poll_alerts(bot, api) == 1
    assert await poll_alerts(bot, api) == 0
