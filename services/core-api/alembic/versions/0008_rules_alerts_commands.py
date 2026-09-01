"""Правила, алерты и аудит команд на трекер.

Дедуп открытых алертов — на уровне БД двумя частичными индексами: правила
дергаются из нескольких точек (таймер, ингест телеметрии, джоба команд),
и гонка иначе плодит дубли. Вставка идёт ON CONFLICT DO UPDATE, поэтому
triggered_at остаётся моментом первого срабатывания, а свежесть снимка
показывает last_seen_at (plan/02).

Revision ID: 0008_rules_alerts_commands
Revises: 0007_fines_maintenance
Create Date: 2026-09-01
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0008_rules_alerts_commands"
down_revision: Union[str, None] = "0007_fines_maintenance"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_RULE_TYPES = ("overdue_payment", "fines_count", "maintenance_km")
_ALERT_TYPES = _RULE_TYPES + ("command_unconfirmed", "odometer_untrusted")
_COMMAND_TYPES = ("engine_stop", "engine_resume", "alarm_arm", "alarm_disarm")
_COMMAND_STATUSES = (
    "queued",
    "blocked_by_safety",
    "sent",
    "acked",
    "unconfirmed",
    "failed",
)


def upgrade() -> None:
    op.create_table(
        "rules",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "car_id",
            sa.Integer(),
            sa.ForeignKey("cars.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("type", sa.Enum(*_RULE_TYPES, name="rule_type"), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column(
            "severity", sa.String(length=16), nullable=False, server_default="warning"
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
    )

    op.create_table(
        "alerts",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "rule_id",
            sa.Integer(),
            sa.ForeignKey("rules.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "car_id",
            sa.Integer(),
            sa.ForeignKey("cars.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("type", sa.Enum(*_ALERT_TYPES, name="alert_type"), nullable=False),
        sa.Column(
            "severity", sa.String(length=16), nullable=False, server_default="warning"
        ),
        sa.Column(
            "status",
            sa.Enum("open", "acknowledged", "resolved", name="alert_status"),
            nullable=False,
            server_default="open",
        ),
        sa.Column(
            "triggered_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("action_taken", sa.String(length=32), nullable=True),
    )
    op.create_index("ix_alerts_car_id", "alerts", ["car_id"])
    op.create_index("ix_alerts_status", "alerts", ["status"])
    # Дедуп открытых: отдельно для правил и для системных алертов.
    op.create_index(
        "uq_alert_open_rule",
        "alerts",
        ["rule_id", "car_id"],
        unique=True,
        postgresql_where=sa.text("status = 'open' AND rule_id IS NOT NULL"),
        sqlite_where=sa.text("status = 'open' AND rule_id IS NOT NULL"),
    )
    op.create_index(
        "uq_alert_open_sys",
        "alerts",
        ["car_id", "type"],
        unique=True,
        postgresql_where=sa.text("status = 'open' AND rule_id IS NULL"),
        sqlite_where=sa.text("status = 'open' AND rule_id IS NULL"),
    )

    op.create_table(
        "commands",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "car_id",
            sa.Integer(),
            sa.ForeignKey("cars.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "tracker_id",
            sa.Integer(),
            sa.ForeignKey("trackers.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("type", sa.Enum(*_COMMAND_TYPES, name="command_type"), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*_COMMAND_STATUSES, name="command_status"),
            nullable=False,
            server_default="queued",
        ),
        sa.Column("requested_by", sa.BigInteger(), nullable=True),
        sa.Column(
            "alert_id",
            sa.Integer(),
            sa.ForeignKey("alerts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("safety_snapshot", sa.JSON(), nullable=True),
        sa.Column("result", sa.Text(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()
        ),
        sa.Column("acked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_commands_car_id", "commands", ["car_id"])


def downgrade() -> None:
    op.drop_index("ix_commands_car_id", table_name="commands")
    op.drop_table("commands")
    op.drop_index("uq_alert_open_sys", table_name="alerts")
    op.drop_index("uq_alert_open_rule", table_name="alerts")
    op.drop_index("ix_alerts_status", table_name="alerts")
    op.drop_index("ix_alerts_car_id", table_name="alerts")
    op.drop_table("alerts")
    op.drop_table("rules")
    for name in ("command_status", "command_type", "alert_status", "alert_type", "rule_type"):
        sa.Enum(name=name).drop(op.get_bind(), checkfirst=True)
