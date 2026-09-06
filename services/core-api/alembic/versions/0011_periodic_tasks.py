"""Расписания фоновых задач и журнал прогонов.

Частота проверки штрафов должна меняться без передеплоя, поэтому
планировщик читает таблицу, а не код (аналог django-celery-beat).

Журнал прогонов нужен, чтобы отличать «нарушений нет» от «нас не пустили»:
без этого различия заблокированная проверка выглядела бы как успешная и
пустая, и парк молча копил бы штрафы.

Revision ID: 0011_periodic_tasks
Revises: 0010_fine_external_ref_unique
Create Date: 2026-09-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0011_periodic_tasks"
down_revision: Union[str, None] = "0010_fine_external_ref_unique"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "periodic_tasks",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(length=128), nullable=False, unique=True),
        sa.Column("task", sa.String(length=255), nullable=False),
        sa.Column("interval_seconds", sa.Integer(), nullable=True),
        sa.Column("crontab", sa.String(length=64), nullable=True),
        sa.Column("args", sa.JSON(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("last_run_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_run_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_table(
        "task_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("task", sa.String(length=255), nullable=False),
        sa.Column(
            "periodic_task_id",
            sa.Integer(),
            sa.ForeignKey("periodic_tasks.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.Enum("ok", "failed", "refused", name="task_run_status"),
            nullable=False,
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=True),
    )
    op.create_index("ix_task_runs_task_started", "task_runs", ["task", "started_at"])


def downgrade() -> None:
    op.drop_index("ix_task_runs_task_started", table_name="task_runs")
    op.drop_table("task_runs")
    op.drop_table("periodic_tasks")
    sa.Enum(name="task_run_status").drop(op.get_bind(), checkfirst=True)
