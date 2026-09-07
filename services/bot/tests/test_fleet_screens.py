"""Экраны телеметрии, трекера, штрафов и ТО."""
from dataclasses import dataclass, field
from typing import Any


from datetime import datetime, timedelta, timezone

from app.callbacks import FleetCB
from app.handlers import fleet
from app.handlers.fleet import fine_line
from tests.conftest import ADMIN_ID, FakeApi


@dataclass
class FakeMessage:
    answers: list[str] = field(default_factory=list)
    from_user: Any = None

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)


@dataclass
class FakeUser:
    id: int = ADMIN_ID


@dataclass
class FakeCallback:
    message: FakeMessage = field(default_factory=FakeMessage)
    from_user: FakeUser = field(default_factory=FakeUser)

    async def answer(self, text: str | None = None, **kwargs: Any) -> None:
        return None


class FakeState:
    def __init__(self) -> None:
        self.data: dict = {}
        self.state = None
        self.cleared = False

    async def set_state(self, state) -> None:
        self.state = state

    async def update_data(self, **kwargs) -> None:
        self.data.update(kwargs)

    async def get_data(self) -> dict:
        return self.data

    async def clear(self) -> None:
        self.cleared = True


def test_state_text_shows_offline_and_untrusted_odometer():
    text = fleet.state_text(
        {
            "last_ts": "2026-09-01T10:00:00+00:00",
            "online": False,
            "last_point_age_seconds": 7200,
            "lat": 42.87,
            "lon": 74.59,
            "speed_knots": 0.0,
            "ignition": False,
            "odometer_km": "12345.000",
            "odometer_trusted": False,
            "engine_blocked": True,
        }
    )

    assert "нет связи" in text
    assert "2 ч назад" in text
    assert "недостоверен" in text
    assert "заблокирован" in text.lower()


def test_state_text_without_points():
    assert fleet.state_text({"online": False}) == fleet.NO_DATA


async def test_show_state_asks_api():
    api = FakeApi(car_state={"last_ts": None, "online": False})
    callback = FakeCallback()

    await fleet.show_state(callback, FleetCB(action="state", car_id=3), api)

    assert api.called("car_state") == [((3,), {})]
    assert callback.message.answers == [fleet.NO_DATA]


async def test_tracker_binding_warns_about_odometer():
    api = FakeApi(set_tracker={"external_id": "9175358042"})
    state = FakeState()
    state.data["car_id"] = 3
    message = FakeMessage()
    message.text = "9175358042"
    message.from_user = FakeUser()

    await fleet.tracker_set(message, api, state)

    assert api.called("set_tracker")[0][1]["external_id"] == "9175358042"
    assert any("базы пробега" in a or "База пробега" in a for a in message.answers)


async def test_tracker_rejects_non_digits():
    api = FakeApi()
    state = FakeState()
    state.data["car_id"] = 3
    message = FakeMessage()
    message.text = "abc"
    message.from_user = FakeUser()

    await fleet.tracker_set(message, api, state)

    assert api.called("set_tracker") == [], "к API с мусором не ходим"
    assert "только цифры" in message.answers[0]


async def test_fine_form_collects_amount_date_and_note():
    """Дата нарушения важна: по ней правило считает окно штрафов."""
    api = FakeApi(add_fine={"id": 1})
    state = FakeState()
    state.data["car_id"] = 3
    message = FakeMessage()
    message.from_user = FakeUser()

    message.text = "3 000,50"
    await fleet.fine_amount(message, state)
    assert state.data["amount"] == "3000.50"

    message.text = "17.08.2026"
    await fleet.fine_issued_at(message, state)
    assert state.data["issued_at"].startswith("2026-08-17")

    message.text = "АМ1234567 превышение скорости"
    await fleet.fine_note(message, api, state)

    payload = api.called("add_fine")[0][1]
    assert payload["amount"] == "3000.50"
    assert payload["issued_at"].startswith("2026-08-17")
    assert payload["external_ref"] == "АМ1234567"
    assert "превышение" in payload["note"]


async def test_fine_date_rejects_garbage():
    state = FakeState()
    message = FakeMessage()
    message.text = "позавчера"
    message.from_user = FakeUser()

    await fleet.fine_issued_at(message, state)

    assert "не разобрана" in message.answers[0]
    assert "issued_at" not in state.data


async def test_maintenance_done_reports_base_reset():
    api = FakeApi(maintenance_done={"id": 1})
    callback = FakeCallback()

    await fleet.maintenance_done(callback, FleetCB(action="maint_done", car_id=3), api)

    assert api.called("maintenance_done")[0][0] == (3, "oil")
    assert "база пробега" in callback.message.answers[0].lower()


async def test_blocked_car_offers_unblock_button():
    """Заблокированную машину должно быть чем разблокировать из карточки."""
    api = FakeApi(
        car_state={
            "last_ts": "2026-09-02T00:00:00+00:00",
            "online": True,
            "last_point_age_seconds": 10,
            "engine_blocked": True,
        }
    )
    callback = FakeCallback()

    await fleet.show_state(callback, FleetCB(action="state", car_id=3), api)

    markup = fleet.state_keyboard(3, api.responses["car_state"])
    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert labels == ["Разблокировать двигатель"]


async def test_free_car_has_no_unblock_button():
    assert fleet.state_keyboard(3, {"engine_blocked": False}) is None


# --- строка штрафа со скидкой (tolom) ----------------------------------------


def _fine(**over) -> dict:
    fine = {
        "status": "unpaid",
        "issued_at": "2026-08-30T13:08:59+06:00",
        "amount": "1000.00",
        "amount_to_pay": "300.00",
        "discount_days_left": 30,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "note": "Ст. 187 ч. 1 — превышение скорости",
    }
    fine.update(over)
    return fine


def test_fine_line_shows_discount_and_reason():
    line = fine_line(_fine())

    assert "2026-08-30" in line
    assert "1000 сом" in line
    assert "к оплате 300 сом, скидка ещё 30 дн." in line
    assert "превышение скорости" in line


def test_expired_discount_is_not_shown_as_live():
    """Поля скидки снимаются при импорте и не пересчитываются, а время идёт."""
    long_ago = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()

    line = fine_line(_fine(created_at=long_ago))

    assert "скидка ещё" not in line, "через два месяца это была бы неправда"
    assert "к оплате 300 сом" in line
    assert "уточните" in line


def test_discount_line_names_the_date_it_was_taken_on():
    """Цифра скидки верна только на дату проверки — так и пишем."""
    line = fine_line(_fine(created_at="2026-09-05T10:00:00+00:00"))

    assert "2026-09-05" in line or "уточните" in line


def test_service_text_is_escaped():
    """В примечании лежит текст сервиса, а parse_mode=HTML включён глобально."""
    line = fine_line(_fine(note='<a href="http://evil">клик</a> & Co'))

    assert "<a href" not in line
    assert "&lt;a href" in line
    assert "&amp; Co" in line


def test_fine_line_reads_without_discount_fields():
    """Штраф из carcheck и заведённый руками: скидки нет вовсе."""
    line = fine_line({"status": "unpaid", "issued_at": "2026-08-10", "amount": "500.00"})

    assert "500 сом" in line
    assert "к оплате" not in line
    assert "не оплачен" in line


def test_fine_line_without_amount_says_so():
    """carcheck сумм не отдаёт — прочерк выглядел бы как ноль."""
    line = fine_line({"status": "unpaid", "issued_at": "2026-08-10", "amount": None})

    assert "сумма неизвестна" in line
