"""FSM загрузки документа: название → файл."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class DocumentForm(StatesGroup):
    title = State()
    file = State()
