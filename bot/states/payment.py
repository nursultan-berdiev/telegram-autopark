"""FSM-состояния внесения платежа водителем."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class PaymentFlow(StatesGroup):
    waiting_receipt = State()
    confirm = State()
