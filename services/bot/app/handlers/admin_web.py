"""Кнопка «Админка»: одноразовая ссылка на веб-панель.

Ссылку выдаёт core-api, бот только доставляет её тому, кто уже опознан
как админ.
"""
from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.types import Message

from app.client import ApiClient, ApiError
from app.filters import IsAdmin
from app.keyboards.admin import BTN_ADMIN_WEB

log = logging.getLogger(__name__)

router = Router(name="admin_web")
router.message.filter(IsAdmin)


@router.message(F.text == BTN_ADMIN_WEB)
async def send_login_link(message: Message, api: ApiClient) -> None:
    try:
        data = await api.admin_login_link(tg_id=message.from_user.id)
    except ApiError as exc:
        if exc.status_code == 404:
            await message.answer(
                "Веб-админка не настроена: нужны ADMIN_SESSION_SECRET и ADMIN_BASE_URL."
            )
            return
        log.warning("ссылка в админку не выдана: %s", exc)
        await message.answer("Не удалось получить ссылку, попробуйте позже.")
        return

    minutes = data.get("ttl_minutes", 15)
    await message.answer(
        f"Ссылка для входа (одноразовая, {minutes} мин):\n{data['url']}\n\n"
        "Никому её не пересылайте — она открывает админку от вашего имени.",
        disable_web_page_preview=True,
    )
