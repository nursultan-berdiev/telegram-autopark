"""Выход из разговора с ИИ по нажатию кнопки меню.

Разговор «Спросить ИИ» держит FSM-состояние AiQuery.waiting_question и ловит весь
текст как вопросы. Если владелец нажал любую кнопку главного меню — он ушёл в другой
раздел: гасим состояние ДО роутеров, чтобы сам раздел отработал как обычно, а
следующий свободный текст не ушёл снова в ИИ. «Выйти» сюда не входит — её
обрабатывает ai_query в самом состоянии.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.keyboards.admin import ADMIN_MENU_BUTTONS
from app.states.ai_query import AiQuery


class ExitAiConversationMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Callable[[Message, dict[str, Any]], Awaitable[Any]],
        event: Message,
        data: dict[str, Any],
    ) -> Any:
        state: FSMContext | None = data.get("state")
        text = (event.text or "").strip()
        if state is not None and text in ADMIN_MENU_BUTTONS:
            if await state.get_state() == AiQuery.waiting_question.state:
                await state.clear()
        return await handler(event, data)
