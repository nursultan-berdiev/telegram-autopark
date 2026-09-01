"""Фикстуры тестов бота: env и мок ApiClient (в БД бот больше не ходит)."""
from __future__ import annotations

import os
from typing import Any

# Переменные окружения должны быть заданы ДО импорта app.config.
os.environ.setdefault("BOT_TOKEN", "123:TEST")
os.environ.setdefault("ADMIN_IDS", "111,222")
os.environ.setdefault("CORE_API_URL", "http://core-api-test")
os.environ.setdefault("CORE_API_TOKEN", "core-test-token")

import pytest  # noqa: E402

ADMIN_ID = 111
DRIVER_ID = 555


class FakeApi:
    """Мок ApiClient: пишет вызовы и отдаёт заранее заданные ответы.

    Любой метод ApiClient доступен как атрибут: возвращает значение из
    `responses[имя]` (или его результат, если это callable).
    """

    def __init__(self, **responses: Any) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.responses: dict[str, Any] = {
            "me": {"role": "guest", "driver": None},
            "cars": [],
            "drivers": [],
            "alerts": [],
        }
        self.responses.update(responses)

    def set(self, name: str, value: Any) -> None:
        self.responses[name] = value

    def called(self, name: str) -> list[tuple[tuple, dict]]:
        return [(a, k) for n, a, k in self.calls if n == name]

    def __getattr__(self, name: str):
        if name.startswith("_"):
            raise AttributeError(name)

        async def _call(*args: Any, **kwargs: Any):
            self.calls.append((name, args, kwargs))
            value = self.responses.get(name)
            if callable(value):
                return value(*args, **kwargs)
            return value

        return _call


@pytest.fixture
def api() -> FakeApi:
    return FakeApi()


@pytest.fixture
def admin_api(api: FakeApi) -> FakeApi:
    api.set("me", {"role": "admin", "driver": None})
    return api


@pytest.fixture
def driver_api(api: FakeApi) -> FakeApi:
    api.set(
        "me",
        {
            "role": "driver",
            "driver": {
                "id": 1,
                "tg_user_id": DRIVER_ID,
                "full_name": "Тест Водитель",
                "car_id": 1,
                "car_plate": "01KG001AAA",
                "active": True,
            },
        },
    )
    return api


@pytest.fixture(scope="session")
def dispatcher_factory():
    """Диспетчер собирается ОДИН раз за прогон — и только здесь.

    Роутеры-модули aiogram это синглтоны: привязать их ко второму диспетчеру
    нельзя. Поэтому create_dispatcher() нигде больше вызывать нельзя — иначе
    слот займёт тот файл, который коллектится первым, а остальные упадут.
    """
    from app import __main__ as entrypoint

    holder: dict = {}

    def _make(api: "FakeApi"):
        if "dp" not in holder:
            holder["dp"] = entrypoint.create_dispatcher(api)
            holder["api"] = api
            return holder["dp"]
        # Мидлварь роли держит ссылку на первый api — подменяем ответы в нём.
        holder["api"].responses.update(api.responses)
        holder["api"].calls = api.calls
        return holder["dp"]

    return _make
