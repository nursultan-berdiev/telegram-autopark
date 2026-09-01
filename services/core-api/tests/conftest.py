"""Общие фикстуры core-api: env, БД в памяти и ASGI-клиент."""
from __future__ import annotations

import os

# Переменные окружения должны быть заданы ДО импорта app.config.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ADMIN_IDS", "111,222")
os.environ.setdefault("CORE_API_TOKEN", "core-test-token")
os.environ.setdefault("INGEST_TOKEN", "ingest-test-token")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test")

import pytest  # noqa: E402
import pytest_asyncio  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    async_sessionmaker,
    create_async_engine,
)

from app.db.base import Base  # noqa: E402

CORE_TOKEN = os.environ["CORE_API_TOKEN"]
INGEST_TOKEN = os.environ["INGEST_TOKEN"]
ADMIN_ID = 111


@pytest_asyncio.fixture
async def session_maker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest_asyncio.fixture
async def session(session_maker):
    async with session_maker() as s:
        yield s


@pytest_asyncio.fixture
async def client(session_maker):
    """HTTP-клиент поверх ASGI: та же БД в памяти, что и у фикстуры session."""
    import httpx

    from app.db.session import get_session
    from app.main import app

    async def _override():
        async with session_maker() as s:
            yield s

    app.dependency_overrides[get_session] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://api",
        headers={"Authorization": f"Bearer {CORE_TOKEN}"},
    ) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def admin_headers():
    return {"Authorization": f"Bearer {CORE_TOKEN}", "X-TG-User-Id": str(ADMIN_ID)}


@pytest.fixture
def ingest_headers():
    return {"Authorization": f"Bearer {INGEST_TOKEN}"}


@pytest_asyncio.fixture
async def admin_client(client, admin_headers):
    """Клиент с заголовком админа: запись в домен доступна только ему."""
    client.headers.update(admin_headers)
    return client
