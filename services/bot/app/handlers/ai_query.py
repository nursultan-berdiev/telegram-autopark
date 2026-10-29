"""Админ: разговор с ИИ по данным автопарка (FR-AI-6/7).

Разговор, а не одиночные вопросы: «Спросить ИИ» открывает беседу, история её
реплик уходит модели на каждом ходу. Пока беседа идёт, снизу висит «Выйти».
Заканчивается по «Выйти» или по нажатию любой кнопки меню (её гасит
ExitAiConversationMiddleware). Авто-выхода по таймауту нет; новый разговор — с
чистого листа.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

from app.client import ApiClient, ApiError
from app.filters import IsAdmin
from app.keyboards.admin import BTN_AI, BTN_AI_EXIT, admin_menu, ai_conversation_menu
from app.states.ai_query import AiQuery

logger = logging.getLogger(__name__)
router = Router(name="ai_query")
router.message.filter(IsAdmin)

# Предел длины истории (реплик) в одном разговоре: держим контекст, но не даём ему
# расти бесконечно — иначе промпт и расход на подписку раздуваются.
HISTORY_LIMIT = 20


@router.message(F.text == BTN_AI)
async def ai_start(message: Message, state: FSMContext) -> None:
    # Новый разговор всегда с чистого листа (middleware уже погасил прежний).
    await state.set_state(AiQuery.waiting_question)
    await state.update_data(history=[])
    await message.answer(
        "Задайте вопрос по автопарку (например: «кто не оплатил», «сколько машин "
        "свободно», «у кого подошёл срок»). Я помню этот разговор, пока вы его ведёте.\n"
        "Чтобы закончить — «Выйти» или любой раздел меню.",
        reply_markup=ai_conversation_menu(),
    )


@router.message(AiQuery.waiting_question, F.text == BTN_AI_EXIT)
async def ai_exit(message: Message, state: FSMContext) -> None:
    await state.clear()
    await message.answer("Вышли из разговора с ИИ.", reply_markup=admin_menu())


@router.message(AiQuery.waiting_question, F.text)
async def ai_answer(message: Message, state: FSMContext, api: ApiClient) -> None:
    question = message.text.strip()
    if not question:
        return

    data = await state.get_data()
    history = list(data.get("history", []))

    await message.answer("⏳ Думаю...")
    try:
        result = await api.assistant_query(
            question, tg_id=message.from_user.id, history=history
        )
    except ApiError as exc:
        # Разговор не рвём: владелец закончит его сам «Выйти» или кнопкой меню.
        logger.warning("Ошибка ИИ-запроса: %s", exc)
        await message.answer(
            "Не удалось получить ответ от ИИ. Попробуйте ещё раз или «Выйти».",
            reply_markup=ai_conversation_menu(),
        )
        return

    answer = result["answer"]
    # Новый список, а не мутация переданного в запрос: аккуратнее и без алиасинга.
    new_history = history + [
        {"role": "user", "text": question},
        {"role": "assistant", "text": answer},
    ]
    await state.update_data(history=new_history[-HISTORY_LIMIT:])
    await message.answer(answer, reply_markup=ai_conversation_menu())
