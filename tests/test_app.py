"""Тесты конфигурации, ролей и сборки приложения (Этапы 0, 7)."""
from bot.config import settings
from bot.handlers import get_main_router


def test_admin_ids_parsed_from_env():
    assert settings.admin_ids == [111, 222]
    assert settings.is_admin(111) is True
    assert settings.is_admin(999) is False


def test_invite_ttl_default():
    assert settings.invite_ttl_hours == 24


def test_all_routers_assembled():
    router = get_main_router()
    names = [r.name for r in router.sub_routers]
    for expected in (
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
    ):
        assert expected in names, f"роутер {expected} не подключён"


def test_metadata_has_all_tables():
    from bot.db.base import Base
    import bot.db.models  # noqa: F401

    tables = set(Base.metadata.tables)
    assert tables == {
        "cars",
        "drivers",
        "invitations",
        "payment_schedules",
        "payments",
    }
