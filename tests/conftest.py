"""Общие фикстуры тестов: env-переменные и async-сессия SQLite в памяти."""
from __future__ import annotations

import os

# Переменные окружения должны быть заданы ДО импорта bot.config.
os.environ.setdefault("BOT_TOKEN", "123:TEST")
os.environ.setdefault("ADMIN_IDS", "111,222")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/db"
)
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from bot.db.base import Base  # noqa: E402


@pytest.fixture(scope="session")
def dispatcher():
    """Один диспетчер на весь прогон.

    Роутеры — модульные синглтоны: собрать диспетчер дважды нельзя, aiogram
    бросит «Router is already attached».
    """
    from bot import __main__ as app

    return app.create_dispatcher()


@pytest_asyncio.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@pytest_asyncio.fixture
async def session_maker():
    """Фабрика сессий к БД в памяти — для подмены async_session_maker в middleware."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
