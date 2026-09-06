"""Сессия админки: подписанная кука вместо серверного хранилища.

Сессий мало и живут они сутки — отдельная таблица только добавила бы
состояние. Подпись не даёт подделать Telegram-id, а срок жизни зашит в
сам токен, поэтому просроченную куку не примут даже при живом секрете.
"""
from __future__ import annotations

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from app.config import settings
from app.domain.admin_auth import SESSION_TTL

COOKIE_NAME = "autopark_admin"
_SALT = "admin-session"


def _serializer() -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.admin_session_secret, salt=_SALT)


def issue(tg_user_id: int) -> str:
    return _serializer().dumps({"tg": tg_user_id})


def read(cookie: str | None) -> int | None:
    """Telegram-id из куки или None. Любая порча подписи — просто «не вошёл»."""
    if not cookie or not settings.admin_session_secret:
        return None
    try:
        data = _serializer().loads(cookie, max_age=int(SESSION_TTL.total_seconds()))
    except (BadSignature, SignatureExpired):
        return None
    tg = data.get("tg") if isinstance(data, dict) else None
    return int(tg) if isinstance(tg, int) else None
