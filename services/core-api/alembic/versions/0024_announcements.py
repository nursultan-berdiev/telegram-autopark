"""Объявления парка: история для водителей, рассылка — через outbound_messages.

У письма в очереди появляется ссылка на объявление и отметка отмены: удаление
объявления снимает его ещё не ушедшую рассылку, не смешивая это со сбоями доставки.

Revision ID: 0024_announcements
Revises: 0023_digest_time_2210
Create Date: 2026-10-17

NB: id ревизии держим ≤32 символов — `alembic_version.version_num` это varchar(32).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0024_announcements"
down_revision: Union[str, None] = "0023_digest_time_2210"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "announcements",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("created_by", sa.BigInteger(), nullable=False),
        sa.Column("recipients", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_announcements_created_at", "announcements", ["created_at"])

    # batch: SQLite не умеет ALTER TABLE ADD CONSTRAINT, на PostgreSQL это обычный ALTER.
    with op.batch_alter_table("outbound_messages") as batch:
        batch.add_column(sa.Column("announcement_id", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True))
        batch.create_foreign_key(
            "fk_outbound_messages_announcement_id",
            "announcements",
            ["announcement_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch.create_index("ix_outbound_messages_announcement_id", ["announcement_id"])


def downgrade() -> None:
    with op.batch_alter_table("outbound_messages") as batch:
        batch.drop_index("ix_outbound_messages_announcement_id")
        batch.drop_constraint("fk_outbound_messages_announcement_id", type_="foreignkey")
        batch.drop_column("cancelled_at")
        batch.drop_column("announcement_id")
    op.drop_index("ix_announcements_created_at", table_name="announcements")
    op.drop_table("announcements")
