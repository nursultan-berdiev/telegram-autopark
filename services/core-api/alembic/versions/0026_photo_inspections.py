"""Фотоконтроль состояния машины: срок на водителя, сданные осмотры, напоминания.

`photo_schedules` — срок очередного фотоконтроля, `photo_inspections` — сданные
осмотры (4 фото по file_id + отчёт). Новые значения alert_type: просрочка
фотоконтроля и задел авто-блокировки за неё (задача блокировки не заводится).
Строка periodic_tasks для ежедневных напоминаний — идемпотентно, как в 0022.

Revision ID: 0026_photo_inspections
Revises: 0025_documents
Create Date: 2026-10-17

NB: id ревизии держим ≤32 символов — `alembic_version.version_num` это varchar(32).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0026_photo_inspections"
down_revision: Union[str, None] = "0025_documents"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_ALERT_VALUES = (
    "photo_inspection_overdue",
    "photo_block_fired",
    "photo_unblock",
    "photo_unblock_failed",
)
_TASK_NAME = "Напоминание о фотоконтроле"
_TASK = "app.tasks.photo_inspections.notify_photo_inspections"


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        # ADD VALUE нельзя в той же транзакции, где значение используется (как в 0020).
        with op.get_context().autocommit_block():
            for value in _ALERT_VALUES:
                op.execute(f"ALTER TYPE alert_type ADD VALUE IF NOT EXISTS '{value}'")

    op.create_table(
        "photo_schedules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "driver_id",
            sa.Integer(),
            sa.ForeignKey("drivers.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
        ),
        sa.Column("next_due_on", sa.Date(), nullable=False),
        sa.Column("last_reminded_on", sa.Date(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_table(
        "photo_inspections",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "driver_id",
            sa.Integer(),
            sa.ForeignKey("drivers.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "car_id",
            sa.Integer(),
            sa.ForeignKey("cars.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("front_file_id", sa.String(length=256), nullable=False),
        sa.Column("rear_file_id", sa.String(length=256), nullable=False),
        sa.Column("interior_front_file_id", sa.String(length=256), nullable=False),
        sa.Column("interior_rear_file_id", sa.String(length=256), nullable=False),
        sa.Column("has_issues", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_photo_inspections_driver_created",
        "photo_inspections",
        ["driver_id", "created_at"],
    )

    bind.execute(
        sa.text(
            "INSERT INTO periodic_tasks "
            "(name, task, interval_seconds, crontab, args, enabled, "
            " last_run_at, total_run_count, created_at, updated_at) "
            "VALUES (:name, :task, NULL, :crontab, NULL, true, "
            " CURRENT_TIMESTAMP, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
            "ON CONFLICT (name) DO NOTHING"
        ),
        {"name": _TASK_NAME, "task": _TASK, "crontab": "0 10 * * *"},
    )


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM periodic_tasks WHERE name = :name"), {"name": _TASK_NAME}
    )
    op.drop_index("ix_photo_inspections_driver_created", table_name="photo_inspections")
    op.drop_table("photo_inspections")
    op.drop_table("photo_schedules")
    # Значения alert_type PostgreSQL удалить не умеет; они безвредны.
