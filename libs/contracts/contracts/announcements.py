"""DTO объявлений парка и общий для core-api и бота рендер рассылки.

Рендер живёт здесь, а не в каждом сервисе: предпросмотр в боте обязан совпадать
с тем, что core-api положит в очередь, — две копии разошлись бы молча.
"""
from __future__ import annotations

from datetime import datetime
from html import escape

from .common import DTO

# Telegram ограничивает сообщение 4096 символами ПОСЛЕ разбора entities (теги и
# &amp; не в счёт); запас — под заголовок рассылки.
ANNOUNCEMENT_MAX_TEXT = 3500


def telegram_length(text: str) -> int:
    """Длина так, как её меряет Telegram: в UTF-16 code units (эмодзи — две)."""
    return len(text.encode("utf-16-le")) // 2


def render_announcement(text: str) -> str:
    """Текст рассылки и предпросмотра. parse_mode=HTML у бота глобальный — экранируем."""
    return f"📢 <b>Объявление автопарка</b>\n\n{escape(text, quote=False)}"


class AnnouncementCreate(DTO):
    text: str


class AnnouncementDTO(DTO):
    id: int
    text: str
    created_by: int
    recipients: int
    created_at: datetime | None = None
