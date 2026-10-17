"""FSM фотоконтроля: четыре ракурса → отчёт о состоянии → замечания."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class PhotoForm(StatesGroup):
    front = State()
    rear = State()
    interior_front = State()
    interior_rear = State()
    report = State()
    comment = State()
