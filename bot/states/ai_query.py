"""FSM-состояние свободного запроса владельца к ИИ."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AiQuery(StatesGroup):
    waiting_question = State()
