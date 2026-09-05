"""Round-trip всех CallbackData.

Регрессия на боевой баг: у ScheduleCB поле `value` было не-опциональным `str`
с дефолтом "". Пустое значение пакуется как хвостовой разделитель
("sched:pick_driver:1:"), а unpack отдаёт None → ValidationError → фильтр
молча не срабатывает и хендлер НЕ вызывается. В логах при этом пусто, бот
просто "ничего не делает" — что и случилось с назначением графика.

Поэтому проверяем именно то, что реально уходит в Telegram: pack → unpack.
"""
import pytest

from app.callbacks import (
    AlertCB,
    CarCB,
    DriverCB,
    NewDriverCB,
    PaymentCB,
    ReportCB,
    ScheduleCB,
)

CALLBACKS = [
    # Водители (в т.ч. действия над списком, где driver_id не задан)
    DriverCB(action="view", driver_id=1),
    DriverCB(action="fire", driver_id=1),
    DriverCB(action="fire_confirm", driver_id=1),
    DriverCB(action="list_active"),
    DriverCB(action="list_fired"),
    # Графики — оба варианта: с пустым value и с заполненным.
    ScheduleCB(action="pick_driver", driver_id=1),
    ScheduleCB(action="set_period", driver_id=1, value="weekly"),
    ScheduleCB(action="set_period", driver_id=1, value="custom"),
    ScheduleCB(action="start_today", driver_id=1),
    ScheduleCB(action="start_tomorrow", driver_id=1),
    # Машины
    CarCB(action="view", car_id=1),
    CarCB(action="delete", car_id=1),
    CarCB(action="delete_confirm", car_id=1),
    # Приглашение
    NewDriverCB(action="pick_car", car_id=1),
    # Платежи
    PaymentCB(action="confirm"),
    PaymentCB(action="retry"),
    # Отчёты
    ReportCB(kind="cars"),
    ReportCB(kind="upcoming"),
    ReportCB(kind="by_driver"),
    ReportCB(kind="by_car"),
    # Алерты — набор кнопок зависит от типа алерта (plan/05), поэтому у части
    # действий car_id/alert_id не задаются (ack — только по alert_id).
    AlertCB(action="block", alert_id=1, car_id=1),
    AlertCB(action="unblock", alert_id=1, car_id=1),
    AlertCB(action="retry", alert_id=1, car_id=1),
    AlertCB(action="ack", alert_id=1),
    AlertCB(action="maint_done", alert_id=1, car_id=1),
]


@pytest.mark.parametrize("cb", CALLBACKS, ids=lambda c: c.pack())
def test_callback_roundtrip(cb):
    """То, что отдали в кнопку, должно распаковаться обратно без ошибок."""
    packed = cb.pack()
    assert len(packed.encode()) <= 64, "Telegram не примет callback_data длиннее 64 байт"
    assert type(cb).unpack(packed) == cb


def test_schedule_empty_value_unpacks():
    """Ключевой случай: пустой value не должен ломать распаковку."""
    restored = ScheduleCB.unpack(ScheduleCB(action="pick_driver", driver_id=7).pack())
    assert restored.action == "pick_driver"
    assert restored.driver_id == 7
    assert restored.value is None
