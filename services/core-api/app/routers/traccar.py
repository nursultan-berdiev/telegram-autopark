"""SSO в Traccar: веб-админка открывает карту тем же входом (токен Traccar).

Traccar живёт на своём домене со своим логином. Чтобы не заводить второй пароль,
админка берёт у адаптера короткоживущий токен автологина и отдаёт SPA готовый URL
`<traccar>/?token=...` — тот открывает его в новой вкладке. Пароль Traccar
остаётся только в адаптере; наружу уходит лишь токен с коротким сроком жизни.
"""
from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.auth import require_admin_actor
from app.clients import adapter
from app.config import settings

log = logging.getLogger(__name__)
router = APIRouter()


@router.post("/traccar/sso", dependencies=[Depends(require_admin_actor)])
async def traccar_sso() -> JSONResponse:
    """URL карты Traccar с токеном автологина (SPA открывает в новой вкладке).

    POST, а не GET: ручка выдаёт живую сессию в общую учётку Traccar — это
    действие, а не чтение. Под require_admin_actor (админ-гейт + CSRF-заголовок у
    куки) и аудит-middleware пишет мутации, так что в журнале остаётся след, кто
    открыл карту (на GET middleware аудит не писал бы). Токен многоразовый до
    expiration, не «одноразовый»; живёт недолго и уходит в query — ответ не
    кэшируем (Cache-Control: no-store).
    """
    base = settings.traccar_public_url.rstrip("/")
    if not base:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Traccar SSO не настроен"
        )
    if not base.startswith("https://"):
        # По http токен уйдёт открытым текстом (прокси-логи, история). На проде — https.
        log.warning("TRACCAR_PUBLIC_URL без https — токен уйдёт открытым текстом: %s", base)
    try:
        token = await adapter.create_sso_token()
    except adapter.AdapterError as exc:
        log.warning("traccar sso: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="Traccar недоступен"
        ) from exc
    url = f"{base}/?token={quote(token, safe='')}"
    return JSONResponse({"url": url}, headers={"Cache-Control": "no-store"})
