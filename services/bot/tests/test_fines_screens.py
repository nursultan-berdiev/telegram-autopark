"""Экраны штрафов: списки, карточка, изоляция водителя, проверка по кнопке."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


from app.callbacks import FineCB
from app.client import ApiError
from app.handlers import fines as screens
from app.middlewares.role import Role

ADMIN_ID = 111
DRIVER_ID = 222


def _fine(fine_id: int, car_id: int = 1, **over) -> dict:
    fine = {
        "id": fine_id,
        "car_id": car_id,
        "status": "unpaid",
        "amount": "1000.00",
        "amount_to_pay": "300.00",
        "discount_until": "2026-09-30",
        "external_ref": f"02-08-051-01-7-54782{fine_id}",
        "source": "tolom",
        "car_plate": "01KG195API",
    }
    fine.update(over)
    return fine


@dataclass
class FakeMessage:
    answers: list[str] = field(default_factory=list)
    edits: list[str] = field(default_factory=list)
    markups: list[Any] = field(default_factory=list)
    from_user: Any = None

    async def answer(self, text: str, **kwargs: Any) -> "FakeMessage":
        self.answers.append(text)
        self.markups.append(kwargs.get("reply_markup"))
        return self

    async def edit_text(self, text: str, **kwargs: Any) -> None:
        self.edits.append(text)
        self.markups.append(kwargs.get("reply_markup"))


@dataclass
class FakeUser:
    id: int = ADMIN_ID


@dataclass
class FakeCallback:
    message: FakeMessage = field(default_factory=FakeMessage)
    from_user: FakeUser = field(default_factory=FakeUser)
    answers: list[str | None] = field(default_factory=list)

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        self.answers.append(text)


class FakeApi:
    def __init__(self, **data: Any) -> None:
        self.data = data
        self.calls: list[tuple[str, Any]] = []

    async def fleet_fines(self, *, tg_id: int, only_unpaid: bool = True) -> list[dict]:
        self.calls.append(("fleet_fines", tg_id))
        return self.data.get("fleet", [])

    async def fines(self, car_id: int, **kwargs: Any) -> list[dict]:
        self.calls.append(("fines", car_id))
        return self.data.get("car_fines", [])

    async def fine(self, fine_id: int) -> dict:
        self.calls.append(("fine", fine_id))
        found = self.data.get("by_id", {}).get(fine_id)
        if found is None:
            raise ApiError(404, "не найдено")
        return found

    async def pay_fine(self, fine_id: int, *, tg_id: int) -> dict:
        self.calls.append(("pay", fine_id))
        return {**self.data["by_id"][fine_id], "status": "paid", "paid_by": "admin"}

    async def check_fines(self, *, tg_id: int) -> dict:
        self.calls.append(("check", tg_id))
        return {"id": 5}

    async def task_run(self, run_id: int, *, tg_id: int) -> dict:
        self.calls.append(("task_run", run_id))
        return self.data.get("run", {"finished_at": None})


def _labels(markup) -> list[str]:
    return [b.text for row in markup.inline_keyboard for b in row]


# --- список по парку --------------------------------------------------------


async def test_admin_sees_the_whole_fleet_with_plates():
    message = FakeMessage(from_user=FakeUser())
    api = FakeApi(fleet=[_fine(1), _fine(2, car_id=2, car_plate="01KG137API")])

    await screens.fleet_fines(message, api)

    assert "Неоплаченные штрафы парка: 2" in message.answers[0]
    labels = _labels(message.markups[0])
    assert labels[0].startswith("01KG195API · ")
    assert "🔄 Проверить сейчас" in labels


async def test_fleet_screen_reports_api_failure():
    message = FakeMessage(from_user=FakeUser())

    class _Boom(FakeApi):
        async def fleet_fines(self, **kwargs):
            raise ApiError(503, "Сервер недоступен, попробуйте позже.")

    await screens.fleet_fines(message, _Boom())

    assert "Сервер недоступен" in message.answers[0]


# --- список водителя --------------------------------------------------------


async def test_driver_sees_only_own_car():
    message = FakeMessage(from_user=FakeUser(id=DRIVER_ID))
    api = FakeApi(car_fines=[_fine(1)])

    await screens.my_fines(message, api, {"id": 9, "car_id": 42})

    assert ("fines", 42) in api.calls
    assert "Ваши штрафы: 1" in message.answers[0]


async def test_driver_without_car_gets_a_plain_answer():
    message = FakeMessage(from_user=FakeUser(id=DRIVER_ID))
    api = FakeApi()

    await screens.my_fines(message, api, {"id": 9, "car_id": None})

    assert "не закреплена" in message.answers[0]
    assert api.calls == [], "к API не ходим вовсе"


# --- карточка ---------------------------------------------------------------


async def test_card_shows_details_and_pay_button_for_admin():
    callback = FakeCallback()
    api = FakeApi(by_id={1: _fine(1, article="Ст. 187 ч. 1")})

    await screens.show_card(
        callback, FineCB(action="card", fine_id=1, scope="fleet"), api, Role.admin
    )

    assert "Ст. 187 ч. 1" in callback.message.edits[0]
    assert "✅ Отметить оплаченным" in _labels(callback.message.markups[0])


async def test_driver_cannot_open_a_fine_of_another_car():
    callback = FakeCallback(from_user=FakeUser(id=DRIVER_ID))
    api = FakeApi(by_id={7: _fine(7, car_id=99)})

    await screens.show_card(
        callback,
        FineCB(action="card", fine_id=7, scope="mine", ref_id=42),
        api,
        Role.driver,
        {"id": 9, "car_id": 42},
    )

    assert callback.message.edits == []
    assert callback.answers == ["Штраф не найден"]


async def test_forged_ref_id_does_not_open_a_foreign_fine():
    """`ref_id` подставляет клиент: сверять принадлежность с ним бессмысленно."""
    callback = FakeCallback(from_user=FakeUser(id=DRIVER_ID))
    api = FakeApi(by_id={7: _fine(7, car_id=99)})

    await screens.show_card(
        callback,
        # Водитель знает чужой car_id и подставляет его как «свой».
        FineCB(action="card", fine_id=7, scope="mine", ref_id=99),
        api,
        Role.driver,
        {"id": 9, "car_id": 42},
    )

    assert callback.message.edits == []
    assert callback.answers == ["Штраф не найден"]


async def test_driver_opens_own_fine():
    callback = FakeCallback(from_user=FakeUser(id=DRIVER_ID))
    api = FakeApi(by_id={7: _fine(7, car_id=42)})

    await screens.show_card(
        callback,
        FineCB(action="card", fine_id=7, scope="mine", ref_id=42),
        api,
        Role.driver,
        {"id": 9, "car_id": 42},
    )

    assert callback.message.edits, "своя машина — карточка открывается"
    assert "✅ Отметить оплаченным" not in _labels(callback.message.markups[0])


# --- листание ---------------------------------------------------------------


async def test_page_turn_keeps_the_same_list():
    callback = FakeCallback()
    api = FakeApi(fleet=[_fine(i) for i in range(1, 9)])

    await screens.turn_page(
        callback, FineCB(action="page", scope="fleet", page=1), api, Role.admin
    )

    assert "страница 2/2" in callback.message.edits[0]
    assert "◀ Назад" in _labels(callback.message.markups[0])


async def test_driver_cannot_page_through_the_fleet():
    """Иначе любой, кто соберёт callback_data, листает штрафы всего парка."""
    callback = FakeCallback(from_user=FakeUser(id=DRIVER_ID))
    api = FakeApi(fleet=[_fine(i) for i in range(1, 9)])

    await screens.turn_page(
        callback,
        FineCB(action="page", scope="fleet", page=0),
        api,
        Role.driver,
        {"id": 9, "car_id": 42},
    )

    assert callback.message.edits == []
    assert callback.answers == ["Недоступно"]
    assert api.calls == [], "к API не ходим вовсе"


async def test_driver_cannot_page_through_a_foreign_car():
    callback = FakeCallback(from_user=FakeUser(id=DRIVER_ID))
    api = FakeApi(car_fines=[_fine(1, car_id=99)])

    await screens.turn_page(
        callback,
        FineCB(action="page", scope="car", ref_id=99, page=0),
        api,
        Role.driver,
        {"id": 9, "car_id": 42},
    )

    assert callback.answers == ["Недоступно"]
    assert api.calls == []


async def test_driver_pages_own_car_ignoring_the_client_value():
    """Своя машина берётся из роли: подставленный `ref_id` не должен влиять."""
    callback = FakeCallback(from_user=FakeUser(id=DRIVER_ID))
    api = FakeApi(car_fines=[_fine(1)])

    await screens.turn_page(
        callback,
        FineCB(action="page", scope="mine", ref_id=99, page=0),
        api,
        Role.driver,
        {"id": 9, "car_id": 42},
    )

    assert ("fines", 42) in api.calls
    assert ("fines", 99) not in api.calls


# --- проверка по кнопке -----------------------------------------------------


async def test_check_reports_only_new_fines(monkeypatch):
    monkeypatch.setattr(screens.asyncio, "sleep", _no_sleep)
    callback = FakeCallback()
    api = FakeApi(
        run={
            "finished_at": "2026-09-06T16:00:00+00:00",
            "status": "ok",
            "detail": "проверено 2, новых штрафов 1",
            "payload": {"new_fine_ids": [1]},
        },
        fleet=[_fine(1), _fine(2)],
    )

    await screens.check_now(callback, api)

    assert "Новые штрафы: 1" in callback.message.edits[-1]
    # Список берётся одним запросом, а не по запросу на штраф: за первый
    # прогон по парку их бывают десятки.
    assert [c for c in api.calls if c[0] == "fine"] == []
    assert len([c for c in api.calls if c[0] == "fleet_fines"]) == 1


async def test_check_says_when_there_is_nothing_new(monkeypatch):
    monkeypatch.setattr(screens.asyncio, "sleep", _no_sleep)
    callback = FakeCallback()
    api = FakeApi(
        run={
            "finished_at": "2026-09-06T16:00:00+00:00",
            "status": "ok",
            "detail": "проверено 2, новых штрафов 0",
            "payload": {"new_fine_ids": []},
        }
    )

    await screens.check_now(callback, api)

    assert "Новых штрафов нет" in callback.message.edits[-1]


async def test_refused_check_is_not_shown_as_empty(monkeypatch):
    """Отказ сервиса не должен выглядеть как успешная проверка без штрафов."""
    monkeypatch.setattr(screens.asyncio, "sleep", _no_sleep)
    callback = FakeCallback()
    api = FakeApi(
        run={
            "finished_at": "2026-09-06T16:00:00+00:00",
            "status": "refused",
            "detail": "сервис отказал на 01KG195API",
            "payload": {},
        }
    )

    await screens.check_now(callback, api)

    assert "не удалась" in callback.message.edits[-1]
    assert "сервис отказал" in callback.message.edits[-1]


async def test_slow_check_hands_the_user_back(monkeypatch):
    monkeypatch.setattr(screens.asyncio, "sleep", _no_sleep)
    monkeypatch.setattr(screens, "CHECK_WAIT_SECONDS", 6)
    callback = FakeCallback()
    api = FakeApi(run={"finished_at": None})

    await screens.check_now(callback, api)

    assert "дольше обычного" in callback.message.edits[-1]


async def _no_sleep(_seconds: float) -> None:
    return None
