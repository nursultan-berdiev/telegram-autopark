"""Регистрация всех роутеров бота."""
from __future__ import annotations

from aiogram import Router

from app.handlers import (
    ai_query,
    alerts,
    cars,
    common,
    drivers,
    fleet,
    new_driver,
    payments,
    registration,
    reports,
    schedules,
    start,
)


_main_router: Router | None = None


def get_main_router() -> Router:
    """Возвращает корневой роутер (собирается один раз).

    Роутеры модулей — синглтоны и не могут быть привязаны к двум родителям,
    поэтому результат кэшируется: повторный вызов отдаёт тот же экземпляр.
    """
    global _main_router
    if _main_router is not None:
        return _main_router

    router = Router(name="main")
    # common (/cancel) — раньше FSM-хендлеров, чтобы отмена работала всегда.
    router.include_router(common.router)
    # registration — раньше start, чтобы перехватывать deep-link /start <code>.
    router.include_router(registration.router)
    router.include_router(new_driver.router)
    router.include_router(drivers.router)
    router.include_router(cars.router)
    router.include_router(fleet.router)
    router.include_router(schedules.router)
    router.include_router(payments.router)
    router.include_router(reports.router)
    router.include_router(ai_query.router)
    router.include_router(alerts.router)
    router.include_router(start.router)

    _main_router = router
    return router
