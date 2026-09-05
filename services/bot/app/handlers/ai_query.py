"""Админ: свободные вопросы к ИИ по данным автопарка (FR-AI-6/7)."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.client import ApiClient, ApiError
from app.filters import IsAdmin
from app.keyboards.admin import BTN_AI, admin_menu
from app.states.ai_query import AiQuery

logger = logging.getLogger(__name__)
router = Router(name="ai_query")
router.message.filter(IsAdmin)


@router.message(F.text == BTN_AI)
async def ai_start(message: Message, state: FSMContext) -> None:
    await state.set_state(AiQuery.waiting_question)
    await message.answer(
        "Задайте вопрос по автопарку (например: «кто не оплатил», "
        "«сколько машин свободно», «у кого подошёл срок»). Для выхода — /cancel."
    )


@router.message(AiQuery.waiting_question, F.text)
async def ai_answer(message: Message, state: FSMContext, api: ApiClient) -> None:
    question = message.text.strip()
    await message.answer("⏳ Думаю...")

    try:
        result = await api.assistant_query(question)
    except ApiError as exc:
        logger.warning("Ошибка ИИ-запроса: %s", exc)
        await state.clear()
        await message.answer(
            "Не удалось получить ответ от ИИ. Попробуйте позже.",
            reply_markup=admin_menu(),
        )
        return

    # Выходим из режима вопроса, чтобы кнопки меню работали как обычно.
    # Для нового вопроса — снова «Спросить ИИ».
    await state.clear()
    await message.answer(result["answer"], reply_markup=admin_menu())
