"""FSM-состояния настройки графика платежей."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class SetSchedule(StatesGroup):
    interval_days = State()  # только для custom-периода
    amount = State()
    start_date = State()
