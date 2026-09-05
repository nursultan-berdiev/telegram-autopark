"""CRUD правил движка."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor, require_core
from app.db.models import Rule, RuleType
from app.db.session import get_session
from app.errors import DomainError, NotFound
from contracts import RuleDTO, RuleUpsert

router = APIRouter()


def _rule_type(value: str) -> RuleType:
    try:
        return RuleType(value)
    except ValueError as exc:
        raise DomainError(f"неизвестный тип правила: {value}", status_code=422) from exc


@router.get("", response_model=list[RuleDTO])
async def list_rules(
    session: AsyncSession = Depends(get_session), _: str = Depends(require_core)
) -> list[RuleDTO]:
    rules = list(await session.scalars(select(Rule).order_by(Rule.id)))
    return [RuleDTO.model_validate(rule) for rule in rules]


@router.post("", response_model=RuleDTO, status_code=201)
async def create_rule(
    payload: RuleUpsert,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> RuleDTO:
    rule = Rule(
        car_id=payload.car_id,
        type=_rule_type(payload.type),
        params=payload.params,
        enabled=payload.enabled,
        severity=payload.severity,
    )
    session.add(rule)
    await session.commit()
    await session.refresh(rule)
    return RuleDTO.model_validate(rule)


@router.put("/{rule_id}", response_model=RuleDTO)
async def update_rule(
    rule_id: int,
    payload: RuleUpsert,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> RuleDTO:
    rule = await session.get(Rule, rule_id)
    if rule is None:
        raise NotFound("правило не найдено")
    rule.car_id = payload.car_id
    rule.type = _rule_type(payload.type)
    rule.params = payload.params
    rule.enabled = payload.enabled
    rule.severity = payload.severity
    await session.commit()
    await session.refresh(rule)
    return RuleDTO.model_validate(rule)


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(require_admin_actor),
) -> Response:
    rule = await session.get(Rule, rule_id)
    if rule is None:
        raise NotFound("правило не найдено")
    await session.delete(rule)
    await session.commit()
    return Response(status_code=204)
