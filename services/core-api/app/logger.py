"""Логи core-api.

Uvicorn настраивает только свои логгеры: без этого фоновые задачи (правила,
досрочивание команд, чистка телеметрии) работали бы молча.
"""
from __future__ import annotations

import logging
import sys

_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: int = logging.INFO) -> None:
    root = logging.getLogger()
    if any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        root.setLevel(level)
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root.addHandler(handler)
    root.setLevel(level)
    # httpx шумит на каждый запрос к адаптеру и шлюзу.
    logging.getLogger("httpx").setLevel(logging.WARNING)
