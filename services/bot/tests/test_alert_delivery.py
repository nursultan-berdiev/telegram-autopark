"""Доставка алертов ровно один раз, независимо от перезапуска бота.

Раньше дедуп доставки держал бот в памяти (`_delivered`): после перезапуска
все открытые алерты рассылались заново — пугающие повторы «двигатель
заблокирован». Теперь факт доставки хранит core-api (`notified_at`), бот берёт
только ещё не доставленные (`pending_alerts`) и помечает их отправленными.
"""
from __future__ import annotations

import app.alerts as alerts_module
from app.alerts import poll_alerts
from app.client import ApiError


class _Bot:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[int] = []

    async def send_message(self, chat_id: int, text: str, reply_markup=None) -> None:
        if self.fail:
            raise RuntimeError("Forbidden: bot was blocked by the user")
        self.sent.append(chat_id)


class _Api:
    """Подставной core-api: `pending_alerts` отдаёт ещё не помеченные, а
    `mark_alert_notified` имитирует серверную отметку (как после рестарта)."""

    def __init__(self, alerts: list[dict]) -> None:
        self._alerts = list(alerts)
        self.notified: list[int] = []

    async def pending_alerts(self) -> list[dict]:
        return [a for a in self._alerts if a["id"] not in self.notified]

    async def mark_alert_notified(self, alert_id: int) -> dict:
        self.notified.append(alert_id)
        return {}

    async def fines(self, car_id: int, **kwargs) -> list[dict]:
        return []


def _alert(alert_id: int = 1, **over) -> dict:
    alert = {
        "id": alert_id,
        "car_id": 3,
        "car_plate": "01KG777AAA",
        "type": "overdue_payment",
        "severity": "warning",
        "text": "просрочка",
    }
    alert.update(over)
    return alert


async def test_delivered_alert_is_not_repeated(monkeypatch):
    monkeypatch.setattr(alerts_module.settings, "admin_ids", [1, 2])
    bot, api = _Bot(), _Api([_alert()])

    assert await poll_alerts(bot, api) == 2
    assert api.notified == [1], "доставку отметили на сервере"
    assert await poll_alerts(bot, api) == 0, "второй раз то же самое не шлём"


async def test_restart_does_not_resend(monkeypatch):
    """После «перезапуска» бота (новый _Bot, без памяти) повтора нет —
    источник правды серверный `notified_at`, а не память процесса."""
    monkeypatch.setattr(alerts_module.settings, "admin_ids", [1])
    api = _Api([_alert()])

    assert await poll_alerts(_Bot(), api) == 1

    fresh_bot = _Bot()  # как будто бот перезапустили
    assert await poll_alerts(fresh_bot, api) == 0
    assert fresh_bot.sent == [], "перезапуск не должен слать старый алерт заново"


async def test_undelivered_alert_is_retried(monkeypatch):
    """Ни один админ не получил — алерт не помечаем, покажем на следующем проходе."""
    monkeypatch.setattr(alerts_module.settings, "admin_ids", [1])
    api = _Api([_alert()])

    assert await poll_alerts(_Bot(fail=True), api) == 0
    assert api.notified == [], "неудачную доставку отмечать нельзя"

    working = _Bot()
    assert await poll_alerts(working, api) == 1
    assert working.sent == [1]


async def test_partial_delivery_marks_notified(monkeypatch):
    """Один админ заблокировал бота — остальные получили, повтора нет."""
    monkeypatch.setattr(alerts_module.settings, "admin_ids", [1, 2])

    class _Picky(_Bot):
        async def send_message(self, chat_id, text, reply_markup=None):
            if chat_id == 1:
                raise RuntimeError("Forbidden: bot was blocked by the user")
            self.sent.append(chat_id)

    bot, api = _Picky(), _Api([_alert()])

    assert await poll_alerts(bot, api) == 1
    assert api.notified == [1]
    assert await poll_alerts(bot, api) == 0


async def test_armed_block_fired_notifies_admin_and_driver(monkeypatch):
    """Автосработавший взвод: админ видит алерт, водитель — уведомление; один раз."""
    monkeypatch.setattr(alerts_module.settings, "admin_ids", [1])

    class _ApiWithDriver(_Api):
        async def car(self, car_id: int) -> dict:
            return {"id": car_id, "plate": "01KG777AAA", "driver_id": 5}

        async def driver(self, driver_id: int) -> dict:
            return {"driver": {"id": 5, "tg_user_id": 4242}}

    alert = _alert(9, type="armed_block_fired", text="встала — двигатель заблокирован")
    bot, api = _Bot(), _ApiWithDriver([alert])

    assert await poll_alerts(bot, api) == 1
    assert 1 in bot.sent, "админ должен получить алерт"
    assert 4242 in bot.sent, "водитель должен быть уведомлён"
    assert await poll_alerts(bot, api) == 0, "повторно не шлём — ни админу, ни водителю"


async def test_mark_failure_defers_driver_notify(monkeypatch):
    """Серверная отметка не прошла → водителя НЕ уведомляем (иначе дубль каждый
    проход), алерт остаётся в pending и уйдёт на следующем проходе."""
    monkeypatch.setattr(alerts_module.settings, "admin_ids", [1])

    class _ApiNoMark(_Api):
        async def car(self, car_id: int) -> dict:
            return {"id": car_id, "plate": "01KG777AAA", "driver_id": 5}

        async def driver(self, driver_id: int) -> dict:
            return {"driver": {"id": 5, "tg_user_id": 4242}}

        async def mark_alert_notified(self, alert_id: int) -> dict:
            raise ApiError(503, "сервер недоступен")

    alert = _alert(9, type="armed_block_fired", text="встала")
    bot, api = _Bot(), _ApiNoMark([alert])

    assert await poll_alerts(bot, api) == 0, "без отметки доставленным не считаем"
    assert bot.sent == [1], "админу уже отправлено"
    assert 4242 not in bot.sent, "водителя при несработавшей отметке не трогаем"
    assert await api.pending_alerts() == [alert], "алерт остаётся в pending"
