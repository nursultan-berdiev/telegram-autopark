"""FSM публикации объявления: текст → предпросмотр → отправка."""
from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class AnnouncementForm(StatesGroup):
    text = State()
    confirm = State()
