"""core-api: владелец доменной БД и бизнес-логики платформы автопарка."""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.config import settings
from app.errors import DomainError
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
    reminders,
    reports,
    rules,
    schedules,
    telemetry,
    trackers,
)

logger = logging.getLogger("core-api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    scheduler = start_jobs()
    logger.info("core-api запущен (TZ=%s)", settings.timezone)
    try:
        yield
    finally:
        scheduler.shutdown(wait=False)


app = FastAPI(title="Fleet core-api", version="0.1.0", lifespan=lifespan)


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
