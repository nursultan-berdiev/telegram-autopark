"""FSM-состояния регистрации водителя по приглашению."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    full_name = State()
    phone = State()
    inn = State()
    selfie = State()
