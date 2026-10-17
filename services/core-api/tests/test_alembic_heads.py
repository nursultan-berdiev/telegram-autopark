"""Цепочка миграций линейна: у alembic ровно одна голова.

Ветки фич ответвляются от master и каждая добавляет ревизию после общей головы.
Слитые без перебазирования, они дают две головы, и `alembic upgrade head` падает
уже на деплое — ловим это здесь, в CI.
"""
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory

CORE_API = Path(__file__).resolve().parent.parent


def test_single_alembic_head():
    config = Config(str(CORE_API / "alembic.ini"))
    config.set_main_option("script_location", str(CORE_API / "alembic"))

    heads = ScriptDirectory.from_config(config).get_heads()

    assert len(heads) == 1, f"несколько голов alembic: {heads}"
