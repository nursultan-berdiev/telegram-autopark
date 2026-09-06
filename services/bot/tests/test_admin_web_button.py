"""Кнопка «Админка»: ссылка доходит до админа, чужим кнопка недоступна."""
from dataclasses import dataclass, field
from typing import Any

from app.handlers import admin_web
from tests.conftest import ADMIN_ID, FakeApi


@dataclass
class FakeUser:
    id: int = ADMIN_ID


@dataclass
class FakeMessage:
    answers: list[str] = field(default_factory=list)
    from_user: Any = field(default_factory=FakeUser)

    async def answer(self, text: str, **kwargs: Any) -> None:
        self.answers.append(text)


async def test_link_is_sent_with_warning():
    api = FakeApi(
        admin_login_link={"url": "https://autopark.example/admin/login?token=t", "ttl_minutes": 15}
    )
    message = FakeMessage()

    await admin_web.send_login_link(message, api)

    assert "admin/login?token=t" in message.answers[0]
    assert "не пересылайте" in message.answers[0], "ссылка = вход от имени админа"


async def test_missing_config_explains_what_to_set():
    from app.client import ApiError

    def raise_404(*args, **kwargs):
        raise ApiError(404, "админка не настроена")

    api = FakeApi(admin_login_link=raise_404)
    message = FakeMessage()

    await admin_web.send_login_link(message, api)

    assert "ADMIN_SESSION_SECRET" in message.answers[0]


async def test_button_is_admin_only():
    """Фильтр стоит на роутере: иначе ссылку на админку получил бы водитель."""
    from app.filters import RoleFilter

    filters = admin_web.router.message._handler.filters
    assert any(isinstance(f.callback, RoleFilter) for f in filters)
