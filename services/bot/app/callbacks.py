"""CallbackData-фабрики для inline-кнопок."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class CarCB(CallbackData, prefix="car"):
    action: str  # view | delete | delete_confirm
    car_id: int


class NewDriverCB(CallbackData, prefix="newdrv"):
    action: str  # pick_car
    car_id: int


class DriverCB(CallbackData, prefix="drv"):
    action: str  # view | fire | fire_confirm | list_active | list_fired
    driver_id: int = 0  # 0 — для действий над списком


class ScheduleCB(CallbackData, prefix="sched"):
    action: str  # pick_driver | set_period | start_today | start_tomorrow
    driver_id: int
    # Период (daily/weekly/monthly/custom) — только для set_period.
    # ОБЯЗАТЕЛЬНО Optional: пустое поле пакуется как хвостовой разделитель
    # ("sched:pick_driver:1:"), а при распаковке aiogram отдаёт None. Для
    # не-опционального str это ValidationError → фильтр молча не срабатывает,
    # и хендлер не вызывается вообще (без единой строчки в логе).
    value: str | None = None


class PaymentCB(CallbackData, prefix="pay"):
    action: str  # confirm | retry


class ReportCB(CallbackData, prefix="rep"):
    kind: str  # cars | upcoming | by_driver | by_car



class AlertCB(CallbackData, prefix="alr"):
    """Действия по карточке алерта. Набор кнопок зависит от типа (plan/05)."""

    action: str  # block | unblock | retry | ack | maint_done
    alert_id: int = 0
    car_id: int = 0


class FleetCB(CallbackData, prefix="flt"):
    """Телеметрия, трекер, штрафы и ТО по конкретной машине."""

    action: str  # state | tracker | tracker_set | fines | fine_add | fine_pay | maint | maint_set | maint_done
    car_id: int = 0
    obj_id: int = 0
