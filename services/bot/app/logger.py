"""Настройка логирования."""
from __future__ import annotations

import logging


def setup_logging(level: int = logging.INFO) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
    # Приглушаем шумные логгеры сторонних библиотек.
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)
