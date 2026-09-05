"""Тесты сборки приложения: конфиг, роутеры, ApiClient.

БД в боте больше нет (домен уехал в core-api) — таблицы не пиннятся.
"""
from app.client import ApiClient
from app.config import settings
from app.handlers import get_main_router

ROUTER_NAMES = (
    "common",
    "registration",
    "new_driver",
    "drivers",
    "cars",
    "schedules",
    "payments",
    "reports",
    "ai_query",
    "start",
)


def test_admin_ids_parsed_from_env():
    assert settings.admin_ids == [111, 222]
    assert settings.is_admin(111) is True
    assert settings.is_admin(999) is False


def test_all_routers_assembled():
    router = get_main_router()
    names = [r.name for r in router.sub_routers]
    # Не проверяем точное число: другие роутеры (напр. alerts) добавляют
    # соседние агенты — важно лишь, что базовый набор всегда подключён.
    for expected in ROUTER_NAMES:
        assert expected in names, f"роутер {expected} не подключён"


def test_api_client_is_the_data_source():
    """Единственный источник данных теперь ApiClient — прямого доступа к БД нет."""
    api = ApiClient()
    assert isinstance(api, ApiClient)
    for method in (
        "me",
        "cars",
        "car",
        "create_car",
        "delete_car",
        "drivers",
        "driver",
        "register_driver",
        "fire_driver",
        "create_invitation",
        "resolve_invitation",
    ):
        assert hasattr(api, method), f"ApiClient.{method} отсутствует"
