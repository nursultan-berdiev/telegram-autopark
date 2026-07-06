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

logger = logging.getLogger(__name__)


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
    dp = Dispatcher()

    # Middleware ролей — на сообщения и колбэки.
    role_mw = RoleMiddleware()
    dp.message.middleware(role_mw)
    dp.callback_query.middleware(role_mw)

    dp.include_router(get_main_router())

    logger.info("Бот запускается. Админов: %d", len(settings.admin_ids))
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен.")
