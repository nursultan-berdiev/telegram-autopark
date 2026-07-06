"""Регистрация всех роутеров бота."""
from __future__ import annotations

from aiogram import Router

from bot.handlers import (
    ai_query,
    cars,
    common,
    new_driver,
    payments,
    registration,
    reports,
    schedules,
    start,
)


def get_main_router() -> Router:
    router = Router(name="main")
    # common (/cancel) — раньше FSM-хендлеров, чтобы отмена работала всегда.
    router.include_router(common.router)
    # registration — раньше start, чтобы перехватывать deep-link /start <code>.
    router.include_router(registration.router)
    router.include_router(new_driver.router)
    router.include_router(cars.router)
    router.include_router(schedules.router)
    router.include_router(payments.router)
    router.include_router(reports.router)
    router.include_router(ai_query.router)
    router.include_router(start.router)
    return router
