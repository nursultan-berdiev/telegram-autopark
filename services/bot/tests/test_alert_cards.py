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
