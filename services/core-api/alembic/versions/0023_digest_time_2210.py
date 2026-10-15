"""Сдвиг админ-дайджеста 22:05 → 22:10.

Дайджест снимал срез ровно в 22:05 — на хвосте «часа пик» платежей (дедлайн 22:00).
Платёж, пришедший в те же секунды, не успевал попасть в снимок. Сдвигаем крон на
22:10, чтобы оплаты последних секунд успевали записаться.

UPDATE идемпотентный и только по старому значению: если владелец уже поменял крон
в /admin, строку не трогаем.

Revision ID: 0023_digest_time_2210
Revises: 0022_outbox_and_task_rows
Create Date: 2026-10-15

NB: id ревизии держим ≤32 символов — `alembic_version.version_num` это varchar(32)
(PostgreSQL обрезает/падает; sqlite длину не проверяет, поэтому локально не ловится).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0023_digest_time_2210"
down_revision: Union[str, None] = "0022_outbox_and_task_rows"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TASK = "app.tasks.notifications.notify_admin_digest"


def _move(bind, *, frm: str, to: str, name: str) -> None:
    # Правим и расписание, и подпись в /admin — чтобы в панели не осталось «22:05»
    # на задаче, которая идёт в 22:10. Условие по старому крону: если владелец уже
    # менял строку руками, не трогаем её.
    bind.execute(
        sa.text(
            "UPDATE periodic_tasks "
            "SET crontab = :to, name = :name, updated_at = CURRENT_TIMESTAMP "
            "WHERE task = :task AND crontab = :frm"
        ),
        {"to": to, "frm": frm, "name": name, "task": _TASK},
    )


def upgrade() -> None:
    _move(
        op.get_bind(),
        frm="5 22 * * *",
        to="10 22 * * *",
        name="Админ-дайджест (22:10)",
    )


def downgrade() -> None:
    _move(
        op.get_bind(),
        frm="10 22 * * *",
        to="5 22 * * *",
        name="Админ-дайджест (22:05)",
    )
