"""Один незавершённый прогон на задачу.

`POST /fines/check` проверял «идёт ли уже прогон» отдельным запросом и только
потом заводил строку. Два одновременных нажатия «Проверить сейчас» в разных
транзакциях оба видели «не идёт» — и в очередь уходили две задачи, а в
госсервис два обхода парка подряд.

Частичный уникальный индекс делает это невозможным на уровне БД, а не
договорённости: вторая вставка падает, и обработчик отдаёт уже идущий прогон.

Revision ID: 0016_one_running_task
Revises: 0015_fine_details
Create Date: 2026-09-07
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0016_one_running_task"
down_revision: Union[str, None] = "0015_fine_details"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Незавершённые прогоны могли накопиться до этой миграции: закрываем их,
    # иначе индекс не построится, а «вечно идущий» прогон навсегда заблокирует
    # кнопку.
    op.execute(
        "UPDATE task_runs SET finished_at = started_at, "
        "detail = COALESCE(detail, '') || ' (закрыт миграцией 0016)' "
        "WHERE finished_at IS NULL"
    )
    op.create_index(
        "uq_task_run_active",
        "task_runs",
        ["task"],
        unique=True,
        sqlite_where=sa.text("finished_at IS NULL"),
        postgresql_where=sa.text("finished_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_task_run_active", table_name="task_runs")
