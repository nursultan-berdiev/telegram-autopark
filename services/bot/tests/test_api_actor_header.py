"""Кто меняет данные — тот представляется.

core-api проверяет админа по заголовку `X-TG-User-Id` на каждом изменяющем
маршруте. Если клиент бота его не шлёт, сервер отвечает 403, и кнопка просто
не работает — ровно так пять дней не добавлялась машина: проверку админа на
`POST /cars` добавили при переезде на платформу, а клиент не поправили.

Ни один тест этого не ловил: тесты бота ходят через подставной ApiClient, а
тесты core-api зовут API напрямую с готовым заголовком. Здесь склейка и
проверяется — настоящим клиентом поверх подставного транспорта.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import httpx
import pytest

from app.client import ApiClient
from app.handlers import schedules
from tests.conftest import FakeApi, FakeCallback, FakeMessage, FakeState, FakeUser

# Синтетическое значение: настоящий Telegram ID администратора в публичном
# репозитории не нужен.
ACTOR = 111111111


def _client(seen: dict) -> ApiClient:
    def handler(request: httpx.Request) -> httpx.Response:
        seen["actor"] = request.headers.get("X-TG-User-Id")
        return httpx.Response(200, json={})

    api = ApiClient(base_url="http://core-api", token="t")
    api._client = httpx.AsyncClient(
        base_url="http://core-api", transport=httpx.MockTransport(handler)
    )
    return api


# Изменяющие вызовы, которые core-api пускает только от администратора.
# Список сверяется глазами с маршрутами под `require_admin_actor`.
ADMIN_CALLS = [
    ("create_car", lambda api: api.create_car(tg_id=ACTOR, plate="01KG100AAA")),
    ("delete_car", lambda api: api.delete_car(1, tg_id=ACTOR)),
    ("fire_driver", lambda api: api.fire_driver(1, tg_id=ACTOR)),
    (
        "create_invitation",
        lambda api: api.create_invitation(1, tg_id=ACTOR),
    ),
    (
        "set_schedule",
        lambda api: api.set_schedule(
            1,
            tg_id=ACTOR,
            period="monthly",
            amount=Decimal("1000"),
            next_due_date=datetime(2026, 9, 10, tzinfo=timezone.utc),
        ),
    ),
    ("set_tracker", lambda api: api.set_tracker(1, tg_id=ACTOR, external_id="9175358042")),
    ("add_fine", lambda api: api.add_fine(1, tg_id=ACTOR, amount=100)),
    ("pay_fine", lambda api: api.pay_fine(1, tg_id=ACTOR)),
    (
        "set_maintenance",
        lambda api: api.set_maintenance(1, tg_id=ACTOR, type="oil", interval_km=10000),
    ),
    ("maintenance_done", lambda api: api.maintenance_done(1, "oil", tg_id=ACTOR)),
    (
        "command",
        lambda api: api.command(1, type="engine_block", tg_id=ACTOR),
    ),
    ("admin_login_link", lambda api: api.admin_login_link(tg_id=ACTOR)),
]


@pytest.mark.parametrize("name,call", ADMIN_CALLS, ids=[c[0] for c in ADMIN_CALLS])
async def test_admin_call_sends_actor(name, call):
    seen: dict = {}
    api = _client(seen)

    await call(api)

    assert seen["actor"] == str(ACTOR), (
        f"{name}: без X-TG-User-Id core-api ответит 403, и кнопка молча перестанет работать"
    )
    await api.aclose()


async def test_reading_calls_do_not_need_an_actor():
    """Чтения ходят под сервисным токеном — заголовок им не нужен."""
    seen: dict = {}
    api = _client(seen)

    await api.cars()

    assert seen["actor"] is None
    await api.aclose()


# --- актор на уровне хендлеров ----------------------------------------------
#
# Проверки выше говорят только о сигнатурах ApiClient. Отдельный класс ошибок
# живёт в хендлерах: у сообщения из `callback.message` `from_user` — это сам
# бот, а не нажавший кнопку человек. Вывести из него актора нельзя, и такой
# вызов снова упрётся в 403.

BOT_ID = 8087865304

SCHEDULE_RESPONSE = {
    "schedule": {"id": 1},
    "status": {
        "period_label": "раз в месяц",
        "amount": "1000",
        "next_due_date": "2026-10-01T00:00:00+00:00",
        "is_overdue": False,
    },
}


@pytest.mark.parametrize("handler_name", ["start_today", "start_tomorrow"])
async def test_quick_start_buttons_send_the_human_not_the_bot(handler_name):
    """Кнопки «Сегодня»/«Завтра» — самый частый путь задания графика."""
    api = FakeApi(set_schedule=SCHEDULE_RESPONSE)
    state = FakeState()
    await state.update_data(
        driver_id=1, period="monthly", amount=Decimal("1000"), interval_days=None
    )
    callback = FakeCallback(
        message=FakeMessage(from_user=FakeUser(id=BOT_ID)),
        from_user=FakeUser(id=ACTOR),
    )

    await getattr(schedules, handler_name)(callback, state, api)

    actor = api.called("set_schedule")[0][1]["tg_id"]
    assert actor == ACTOR, "в core-api ушёл бы tg_id бота, и тот ответил бы 403"
    assert actor != BOT_ID
