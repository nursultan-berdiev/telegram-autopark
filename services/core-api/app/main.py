"""core-api: владелец доменной БД и бизнес-логики платформы автопарка."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response

from app.config import settings
from app.errors import DomainError
from app.web.deps import NotLoggedIn
from app.jobs import start_jobs
from app.logger import setup_logging
from app.routers import (
    alerts,
    assistant,
    cars,
    commands,
    drivers,
    fines,
    maintenance,
    invitations,
    me,
    payments,
    admin,
    periodic,
    reminders,
    reports,
    rules,
    schedules,
    telemetry,
    trackers,
)

logger = logging.getLogger("core-api")

# MCP-сервер ассистента подключаем ЛЕНИВО (ниже) — только когда фича включена и
# задан токен. Иначе core-api не зависит от пакета mcp, а ASSISTANT_USE_TOOLS=0 —
# настоящий откат (при несовместимости mcp сервис всё равно стартует).
_fleet_mcp = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    scheduler = start_jobs()
    logger.info("core-api запущен (TZ=%s)", settings.timezone)
    try:
        if _fleet_mcp is not None:
            # streamable-HTTP MCP нужен запущенный менеджер сессий.
            async with _fleet_mcp.session_manager.run():
                yield
        else:
            yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Fleet core-api", version="0.1.0", lifespan=lifespan)


@app.exception_handler(NotLoggedIn)
async def _not_logged_in(request: Request, _: NotLoggedIn) -> Response:
    """На страницах это редирект на объяснение, а не голый 401."""
    if request.url.path.startswith("/admin"):
        return RedirectResponse(url="/admin/login-required", status_code=303)
    return JSONResponse(status_code=401, content={"detail": "нужен вход"})


@app.exception_handler(DomainError)
async def _domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail})


@app.get("/health")
async def health() -> dict:
    return {"ok": True, "service": "core-api"}


app.include_router(me.router, tags=["me"])
app.include_router(cars.router, prefix="/cars", tags=["cars"])
app.include_router(drivers.router, prefix="/drivers", tags=["drivers"])
app.include_router(invitations.router, tags=["invitations"])
app.include_router(schedules.router, tags=["schedules"])
app.include_router(payments.router, tags=["payments"])
app.include_router(reports.router, prefix="/reports", tags=["reports"])
app.include_router(assistant.router, prefix="/assistant", tags=["assistant"])
app.include_router(reminders.router, prefix="/reminders", tags=["reminders"])
app.include_router(telemetry.router, tags=["telemetry"])
app.include_router(trackers.router, tags=["trackers"])
app.include_router(rules.router, prefix="/rules", tags=["rules"])
app.include_router(alerts.router, prefix="/alerts", tags=["alerts"])
app.include_router(commands.router, tags=["commands"])
app.include_router(fines.router, tags=["fines"])
app.include_router(maintenance.router, tags=["maintenance"])
app.include_router(periodic.router, tags=["periodic"])
app.include_router(admin.router, tags=["admin"])

# MCP-сервер ИИ-ассистента (read-only). Монтируем только при включённой фиче и
# заданном токене; /mcp закрыт bearer-токеном (чистая ASGI-обёртка, fail-closed).
if settings.assistant_use_tools and settings.mcp_token:
    from app.assistant.auth import MCPAuth
    from app.assistant.mcp_server import mcp as _fleet_mcp
    from app.assistant.mcp_server import mcp_app as _fleet_mcp_app

    app.mount("/mcp", MCPAuth(_fleet_mcp_app, settings.mcp_token))
