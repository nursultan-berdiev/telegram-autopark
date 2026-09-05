"""Точка входа: запуск бота в режиме long-polling."""
from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from bot.config import settings
from bot.handlers import get_main_router
from bot.logger import setup_logging
from bot.middlewares.role import RoleMiddleware
from bot.scheduler import setup_scheduler

logger = logging.getLogger(__name__)


def create_dispatcher() -> Dispatcher:
    """Собирает диспетчер: middleware ролей + роутеры.

    RoleMiddleware обязательно OUTER: фильтры ролей (IsAdmin/IsDriver) читают
    data["role"], а inner-middleware выполняется уже ПОСЛЕ фильтров — тогда
    фильтр падает с TypeError на каждом апдейте.
    """
    dp = Dispatcher()

    role_mw = RoleMiddleware()
    dp.message.outer_middleware(role_mw)
    dp.callback_query.outer_middleware(role_mw)

    dp.include_router(get_main_router())
    return dp


async def main() -> None:
    setup_logging()

    # Папка для загружаемых файлов (фото машин, селфи, чеки).
    settings.files_dir.mkdir(parents=True, exist_ok=True)

    if not settings.admin_ids:
        logger.warning("ADMIN_IDS пуст — ни один пользователь не будет администратором!")

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = create_dispatcher()
    scheduler = setup_scheduler(bot)  # ежедневные напоминания о платежах

    logger.info("Бот запускается. Админов: %d", len(settings.admin_ids))
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        if scheduler is not None:
            scheduler.shutdown(wait=False)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
