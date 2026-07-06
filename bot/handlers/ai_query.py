"""Админ: свободные вопросы к ИИ по данным автопарка (FR-AI-6/7)."""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message
from sqlalchemy.ext.asyncio import AsyncSession

from bot.filters import IsAdmin
from bot.keyboards.admin import BTN_AI, admin_menu
from bot.services import ai as ai_service
from bot.services import reports as reports_service
from bot.states.ai_query import AiQuery

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
async def ai_answer(
    message: Message, state: FSMContext, session: AsyncSession
) -> None:
    question = message.text.strip()
    await message.answer("⏳ Думаю...")

    context_text = await reports_service.build_snapshot(session)
    try:
        answer = await ai_service.answer_owner_query(question, context_text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка ИИ-запроса: %s", exc)
        await state.clear()
        await message.answer(
            "Не удалось получить ответ от ИИ. Попробуйте позже.",
            reply_markup=admin_menu(),
        )
        return

    # Выходим из режима вопроса, чтобы кнопки меню работали как обычно.
    # Для нового вопроса — снова «Спросить ИИ».
    await state.clear()
    await message.answer(answer, reply_markup=admin_menu())
