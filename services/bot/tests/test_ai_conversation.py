"""Разговор с ИИ: память в рамках беседы, выход по «Выйти», выход по кнопке меню.

Прямые вызовы хендлеров и middleware (без диспетчера): единственный на процесс
диспетчер держит test_dispatch, второй потребитель фабрики его ломает.
"""
from __future__ import annotations

from types import SimpleNamespace

from app.handlers import ai_query
from app.keyboards.admin import BTN_AI_EXIT, BTN_CARS
from app.middlewares.exit_ai import ExitAiConversationMiddleware
from app.states.ai_query import AiQuery
from tests.conftest import ADMIN_ID, FakeApi, FakeMessage, FakeState, FakeUser


def _msg(text: str) -> FakeMessage:
    m = FakeMessage(from_user=FakeUser(id=ADMIN_ID))
    m.text = text
    return m


def _api() -> FakeApi:
    api = FakeApi()
    # Ответ зависит от длины истории — так виден контекст, переданный модели.
    api.set("assistant_query", lambda q, *, tg_id, history: {"answer": f"ответ[{len(history)}]: {q}"})
    return api


async def test_ai_start_opens_conversation_with_empty_history():
    state = FakeState()
    msg = _msg("🤖 Спросить ИИ")
    await ai_query.ai_start(msg, state)
    assert state.state == AiQuery.waiting_question
    assert state.data["history"] == []
    assert any("Выйти" in a for a in msg.answers)  # подсказка про выход показана


async def test_conversation_accumulates_history_and_stays():
    api = _api()
    state = FakeState()
    await ai_query.ai_start(_msg("🤖 Спросить ИИ"), state)

    await ai_query.ai_answer(_msg("сколько машин?"), state, api)
    await ai_query.ai_answer(_msg("а свободных?"), state, api)

    calls = api.called("assistant_query")
    assert len(calls) == 2
    assert calls[0][1]["history"] == []  # первый ход — пусто
    hist2 = calls[1][1]["history"]  # второй ход — первый вопрос и ответ
    assert [h["role"] for h in hist2] == ["user", "assistant"]
    assert hist2[0]["text"] == "сколько машин?"
    assert hist2[1]["text"].startswith("ответ[0]:")
    assert calls[0][1]["tg_id"] == ADMIN_ID
    # Разговор не закрыт — состояние на месте, история из 4 реплик.
    assert state.cleared is False
    assert len(state.data["history"]) == 4


async def test_history_capped():
    api = _api()
    state = FakeState()
    await ai_query.ai_start(_msg("🤖 Спросить ИИ"), state)
    for i in range(ai_query.HISTORY_LIMIT):  # заведомо больше предела в репликах
        await ai_query.ai_answer(_msg(f"вопрос {i}"), state, api)
    assert len(state.data["history"]) == ai_query.HISTORY_LIMIT


async def test_error_keeps_conversation_open():
    from app.client import ApiError

    def _boom(q, *, tg_id, history):
        raise ApiError(502, "gateway")

    api = FakeApi()
    api.set("assistant_query", _boom)
    state = FakeState()
    await ai_query.ai_start(_msg("🤖 Спросить ИИ"), state)
    msg = _msg("вопрос")
    await ai_query.ai_answer(msg, state, api)
    # Ошибка не рвёт разговор: состояние не очищено, пользователю — понятный текст.
    assert state.cleared is False
    assert any("Не удалось" in a for a in msg.answers)


async def test_exit_button_clears_state():
    state = FakeState()
    await ai_query.ai_start(_msg("🤖 Спросить ИИ"), state)
    msg = _msg(BTN_AI_EXIT)
    await ai_query.ai_exit(msg, state)
    assert state.cleared is True
    assert any("Вышли из разговора" in a for a in msg.answers)


# --- middleware выхода по кнопке меню ----------------------------------------


class _FakeStateMW:
    def __init__(self, state) -> None:
        self._state = state
        self.cleared = False

    async def get_state(self):
        return self._state

    async def clear(self):
        self.cleared = True
        self._state = None


async def test_exit_middleware_clears_only_on_menu_button_in_ai_state():
    mw = ExitAiConversationMiddleware()
    seen = {"n": 0}

    async def handler(event, data):
        seen["n"] += 1
        return "ok"

    # В разговоре + кнопка меню → гасим состояние, но хендлер всё равно вызываем.
    st = _FakeStateMW(AiQuery.waiting_question.state)
    await mw(handler, SimpleNamespace(text=BTN_CARS), {"state": st})
    assert st.cleared is True and seen["n"] == 1

    # В разговоре + свободный текст (не кнопка) → не трогаем.
    st2 = _FakeStateMW(AiQuery.waiting_question.state)
    await mw(handler, SimpleNamespace(text="сколько машин?"), {"state": st2})
    assert st2.cleared is False

    # Кнопку «Выйти» middleware не трогает — её гасит сам ai_query.
    st3 = _FakeStateMW(AiQuery.waiting_question.state)
    await mw(handler, SimpleNamespace(text=BTN_AI_EXIT), {"state": st3})
    assert st3.cleared is False

    # Не в разговоре + кнопка меню → ничего не чистим.
    st4 = _FakeStateMW(None)
    await mw(handler, SimpleNamespace(text=BTN_CARS), {"state": st4})
    assert st4.cleared is False
