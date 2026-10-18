"""FSM фотоконтроля: шесть ракурсов → отчёт о состоянии → замечания."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class PhotoForm(StatesGroup):
    front = State()
    left = State()
    rear = State()
    right = State()
    interior_front = State()
    interior_rear = State()
    report = State()
    comment = State()
