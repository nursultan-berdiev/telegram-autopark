"""Точка входа: запуск бота в режиме long-polling."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from app.client import ApiClient
from app.config import settings
from app.handlers import get_main_router
from app.logger import setup_logging
from app.middlewares.role import RoleMiddleware
from app.scheduler import setup_scheduler

logger = logging.getLogger(__name__)


def create_dispatcher(api: ApiClient | None = None) -> Dispatcher:
    """Собирает диспетчер: middleware ролей + роутеры.

    RoleMiddleware обязательно OUTER: фильтры ролей (IsAdmin/IsDriver) читают
    data["role"], а inner-middleware выполняется уже ПОСЛЕ фильтров — тогда
    фильтр падает с TypeError на каждом апдейте.
    """
    dp = Dispatcher()

    role_mw = RoleMiddleware(api)
    dp.message.outer_middleware(role_mw)
    dp.callback_query.outer_middleware(role_mw)

    dp.include_router(get_main_router())
    return dp


async def main() -> None:
    setup_logging()

    if not settings.admin_ids:
        logger.warning("ADMIN_IDS пуст — ни один пользователь не будет администратором!")

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    api = ApiClient()
    dp = create_dispatcher(api)
    scheduler = setup_scheduler(bot, api)  # напоминания и опрос алертов

    logger.info("Бот запускается. Админов: %d", len(settings.admin_ids))
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)
        await api.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
