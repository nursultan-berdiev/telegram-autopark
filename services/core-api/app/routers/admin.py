"""Веб-админка: расписания фоновых задач и журнал прогонов.

Вход — по одноразовой ссылке из бота: пароля у админов нет, а личность уже
подтверждена Telegram.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import require_admin_actor
from app.config import settings
from app.db.session import get_session
from app.domain import admin_auth
from app.domain import periodic as periodic_service
from app.errors import DomainError, NotFound, Validation
from app.web import session as web_session
from app.web.deps import current_admin

log = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "web" / "templates"))

# Задачи, которые вообще разрешено ставить в расписание. Свободный ввод имени
# означал бы запуск произвольной точки входа из формы в браузере.
KNOWN_TASKS = {
    "app.tasks.fines.check_fines": "Проверка штрафов по парку",
    "app.tasks.ping.ping": "Пробник (проверка связки beat → воркер)",
}


def _utc(value: datetime | None) -> datetime | None:
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


# --- вход -------------------------------------------------------------------


@router.post("/admin/login-link")
async def create_login_link(
    session: AsyncSession = Depends(get_session),
    actor: int = Depends(require_admin_actor),
) -> dict:
    """Бот просит ссылку для входа — сам он уже знает, что это админ."""
    if not settings.admin_web_enabled:
        raise NotFound("админка не настроена")
    token, expires_at = await admin_auth.issue_token(session, actor)
    base = settings.admin_base_url.rstrip("/")
    return {
        "url": f"{base}/admin/login?token={token}",
        "expires_at": expires_at,
        "ttl_minutes": int(admin_auth.TOKEN_TTL.total_seconds() // 60),
    }


@router.get("/admin/login", response_class=HTMLResponse)
async def login(
    request: Request,
    token: str = "",
    session: AsyncSession = Depends(get_session),
) -> Response:
    tg_user_id = await admin_auth.redeem_token(session, token) if token else None
    if tg_user_id is None or not settings.is_admin(tg_user_id):
        return templates.TemplateResponse(
            request, "login_failed.html", {"ttl": admin_auth.TOKEN_TTL}, status_code=403
        )
    response = RedirectResponse(url="/admin", status_code=303)
    response.set_cookie(
        web_session.COOKIE_NAME,
        web_session.issue(tg_user_id),
        httponly=True,
        samesite="lax",
        # Куку с правами админа нельзя отдавать по открытому каналу.
        secure=settings.admin_base_url.startswith("https://"),
        max_age=int(admin_auth.SESSION_TTL.total_seconds()),
    )
    return response


@router.get("/admin/login-required", response_class=HTMLResponse)
async def login_required(request: Request) -> Response:
    return templates.TemplateResponse(
        request, "login_failed.html", {"ttl": admin_auth.TOKEN_TTL}, status_code=401
    )


@router.get("/admin/logout")
async def logout() -> Response:
    response = RedirectResponse(url="/admin/login-required", status_code=303)
    response.delete_cookie(web_session.COOKIE_NAME)
    return response


# --- страницы ---------------------------------------------------------------


@router.get("/admin", response_class=HTMLResponse)
async def index(
    request: Request,
    error: str | None = None,
    msg: str | None = None,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(current_admin),
) -> Response:
    tasks = await periodic_service.list_tasks(session)
    runs = await periodic_service.list_runs(session, limit=20)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "tasks": tasks,
            "runs": runs,
            "known_tasks": KNOWN_TASKS,
            "error": error,
            "msg": msg,
            "utc": _utc,
        },
    )


def _period(interval: str, crontab: str) -> tuple[int | None, str | None]:
    """Форма всегда шлёт оба поля — пустое означает «не задано».

    Нецифровой интервал приходит не только из браузера (`type=number`
    защищает лишь его): curl и прокси шлют что угодно, а голый int() отдал
    бы 500 вместо понятной ошибки на странице.
    """
    interval = (interval or "").strip()
    crontab = (crontab or "").strip()
    if interval and not interval.isdigit():
        raise Validation(f"интервал должен быть числом, а не {interval!r}")
    return (int(interval) if interval else None), (crontab or None)


@router.post("/admin/schedules")
async def create_schedule(
    name: str = Form(...),
    task: str = Form(...),
    interval_seconds: str = Form(""),
    crontab: str = Form(""),
    session: AsyncSession = Depends(get_session),
    _: int = Depends(current_admin),
) -> Response:
    if task not in KNOWN_TASKS:
        return RedirectResponse(url="/admin?error=неизвестная+задача", status_code=303)
    try:
        interval, cron = _period(interval_seconds, crontab)
        await periodic_service.create_task(
            session, name=name.strip(), task=task, interval_seconds=interval, crontab=cron
        )
    except DomainError as exc:
        return RedirectResponse(url=f"/admin?error={exc.detail}", status_code=303)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/schedules/{task_id}/toggle")
async def toggle_schedule(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(current_admin),
) -> Response:
    row = await periodic_service.get_task(session, task_id)
    if row is None:
        raise NotFound(f"расписание {task_id} не найдено")
    await periodic_service.update_task(session, task_id, enabled=not row.enabled)
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/schedules/{task_id}/period")
async def edit_period(
    task_id: int,
    interval_seconds: str = Form(""),
    crontab: str = Form(""),
    session: AsyncSession = Depends(get_session),
    _: int = Depends(current_admin),
) -> Response:
    try:
        interval, cron = _period(interval_seconds, crontab)
        updated = await periodic_service.update_task(
            session, task_id, interval_seconds=interval, crontab=cron
        )
    except DomainError as exc:
        return RedirectResponse(url=f"/admin?error={exc.detail}", status_code=303)
    if updated is None:
        raise NotFound(f"расписание {task_id} не найдено")
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/schedules/{task_id}/delete")
async def delete_schedule(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(current_admin),
) -> Response:
    if not await periodic_service.delete_task(session, task_id):
        raise NotFound(f"расписание {task_id} не найдено")
    return RedirectResponse(url="/admin", status_code=303)


@router.post("/admin/schedules/{task_id}/run")
async def run_now(
    task_id: int,
    session: AsyncSession = Depends(get_session),
    _: int = Depends(current_admin),
) -> Response:
    row = await periodic_service.get_task(session, task_id)
    if row is None:
        raise NotFound(f"расписание {task_id} не найдено")
    from app.tasks.celery_app import celery_app

    try:
        # retry=False: недоступный брокер должен сказать об этом сразу,
        # а не держать страницу, пока celery перебирает попытки.
        celery_app.send_task(
            row.task, kwargs={"periodic_task_id": row.id}, retry=False
        )
    except Exception as exc:  # noqa: BLE001 — брокер лежит, это ответ странице
        log.warning("задача %s не поставлена в очередь", row.task, exc_info=True)
        return RedirectResponse(
            url=f"/admin?error=очередь+недоступна:+{type(exc).__name__}", status_code=303
        )
    return RedirectResponse(url="/admin?msg=задача+поставлена+в+очередь", status_code=303)
