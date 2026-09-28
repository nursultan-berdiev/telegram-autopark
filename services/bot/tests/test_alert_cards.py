"""Карточка алерта: набор кнопок зависит от типа.

Кнопка «Заблокировать» на command_unconfirmed провоцировала бы повторную
блокировку поверх неподтверждённой (plan/05, T2).
"""
from app.alerts import alert_keyboard, alert_text


def _alert(atype: str, **over) -> dict:
    alert = {
        "id": 7,
        "car_id": 3,
        "car_plate": "01KG777AAA",
        "type": atype,
        "severity": "warning",
        "text": "что-то случилось",
    }
    alert.update(over)
    return alert


def _labels(alert: dict) -> list[str]:
    markup = alert_keyboard(alert)
    return [button.text for row in markup.inline_keyboard for button in row]


def test_rule_alert_offers_block():
    labels = _labels(_alert("overdue_payment"))

    assert "Заблокировать двигатель" in labels
    assert "Отложить" in labels


def test_unconfirmed_command_has_no_block_button():
    labels = _labels(_alert("command_unconfirmed"))

    assert "Заблокировать двигатель" not in labels
    assert "Повторить" in labels


def test_odometer_alert_offers_maintenance():
    labels = _labels(_alert("odometer_untrusted", severity="info"))

    assert "Заблокировать двигатель" not in labels
    assert "ТО выполнено" in labels


def test_unknown_type_is_informational_only():
    labels = _labels(_alert("что-то новое"))

    assert labels == ["Понятно"]


def test_text_mentions_plate_and_severity():
    text = alert_text(_alert("fines_count", severity="critical"))

    assert "01KG777AAA" in text
    assert "что-то случилось" in text


def test_unconfirmed_unblock_retries_unblock_not_block():
    """Повтор должен повторять ту же команду: иначе разблокировка глушит машину."""
    from app.callbacks import AlertCB

    alert = _alert(
        "command_unconfirmed", payload={"command_type": "engine_resume"}
    )
    markup = alert_keyboard(alert)
    actions = [
        AlertCB.unpack(button.callback_data).action
        for row in markup.inline_keyboard
        for button in row
    ]

    assert "unblock" in actions
    assert "retry" not in actions


def test_unconfirmed_block_retries_block():
    from app.callbacks import AlertCB

    alert = _alert("command_unconfirmed", payload={"command_type": "engine_stop"})
    markup = alert_keyboard(alert)
    actions = [
        AlertCB.unpack(button.callback_data).action
        for row in markup.inline_keyboard
        for button in row
    ]

    assert "retry" in actions


# --- новые штрафы: данные на кнопках ----------------------------------------


class _FakeApi:
    """Бот дозапрашивает штрафы машины и оставляет только новые."""

    def __init__(self, fines: list[dict] | None = None, boom: Exception | None = None):
        self.fines_data = fines or []
        self.boom = boom
        self.asked: list[int] = []

    async def fines(self, car_id: int, **kwargs) -> list[dict]:
        if self.boom is not None:
            raise self.boom
        self.asked.append(car_id)
        return self.fines_data


def _fine(fine_id: int, **over) -> dict:
    fine = {
        "id": fine_id,
        "status": "unpaid",
        "amount": "1000.00",
        "amount_to_pay": "300.00",
        "discount_until": None,
        "external_ref": f"02-08-051-01-7-00000{fine_id}",
        "source": "tolom",
    }
    fine.update(over)
    return fine


async def test_new_fine_alert_shows_a_button_per_fine():
    from app.alerts import new_fine_card

    alert = _alert("new_fine", text="новых штрафов 2:", payload={"fine_ids": [1, 2]})
    api = _FakeApi([_fine(1), _fine(2), _fine(3)])

    text, markup = await new_fine_card(alert, api)

    labels = [b.text for row in markup.inline_keyboard for b in row]
    assert text == "! 01KG777AAA: новых штрафов 2:"
    assert labels == [
        "300 сом (1 000 после окончания скидки)",
        "300 сом (1 000 после окончания скидки)",
    ], "третий штраф не новый — его в списке быть не должно"


async def test_new_fine_alert_survives_api_failure():
    """Уведомление важнее украшений: текст доходит и без кнопок-штрафов."""
    from app.alerts import new_fine_card
    from app.client import ApiError

    alert = _alert("new_fine", text="новых штрафов 1:", payload={"fine_ids": [1]})

    text, markup = await new_fine_card(alert, _FakeApi(boom=ApiError(503, "нет связи")))

    assert "новых штрафов 1" in text
    assert [b.text for row in markup.inline_keyboard for b in row] == ["Понятно"]


async def test_old_alert_without_ids_still_shows():
    """Алерты, поднятые до этой версии, не должны ломать доставку."""
    from app.alerts import new_fine_card

    text, markup = await new_fine_card(_alert("new_fine", payload={}), _FakeApi())

    assert text
    assert [b.text for row in markup.inline_keyboard for b in row] == ["Понятно"]


def test_alert_text_escapes_service_data():
    """Место нарушения от сервиса может содержать угловые скобки."""
    text = alert_text(_alert("new_fine", text="штраф <b>тут</b>"))

    assert "<b>" not in text
    assert "&lt;b&gt;" in text
