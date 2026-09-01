"""FSM-состояния добавления автомобиля."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AddCar(StatesGroup):
    plate = State()
    model = State()
    photo = State()
