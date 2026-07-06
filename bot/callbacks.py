"""CallbackData-фабрики для inline-кнопок."""
from __future__ import annotations

from aiogram.filters.callback_data import CallbackData


class CarCB(CallbackData, prefix="car"):
    action: str  # view | delete | delete_confirm
    car_id: int


class NewDriverCB(CallbackData, prefix="newdrv"):
    action: str  # pick_car
    car_id: int


class ScheduleCB(CallbackData, prefix="sched"):
    action: str  # pick_driver | set_period | start_today | start_tomorrow
    driver_id: int
    value: str = ""  # период (daily/weekly/monthly/custom) при set_period


class PaymentCB(CallbackData, prefix="pay"):
    action: str  # confirm | retry


class ReportCB(CallbackData, prefix="rep"):
    kind: str  # cars | upcoming | by_driver | by_car

