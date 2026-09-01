"""Роутер assistant: снимок автопарка + вопрос владельца в ai_gateway."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_core
from app.clients import ai_gateway
from app.db.session import get_session
from app.errors import DomainError
from app.domain import reports as reports_domain
from contracts import AssistantAnswer, AssistantQuery

router = APIRouter()


@router.post("/query", response_model=AssistantAnswer)
async def query(
    payload: AssistantQuery,
    session: AsyncSession = Depends(get_session),
    _: str = Depends(require_core),
) -> AssistantAnswer:
    snapshot = await reports_domain.build_snapshot(session)
    # Через module-attribute (не прямой импорт функции) — иначе тесты не смогут
    # подменить ai_gateway.answer_owner_query через monkeypatch.
    try:
        answer = await ai_gateway.answer_owner_query(payload.question, snapshot)
    except Exception as exc:  # noqa: BLE001 — сбой ИИ не должен падать 500-кой
        raise DomainError("ИИ-ассистент недоступен", status_code=502) from exc
    return AssistantAnswer(answer=answer)
