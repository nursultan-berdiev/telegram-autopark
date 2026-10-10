"""Очередь исходящих Telegram-сообщений + строки periodic_tasks для уведомлений.

Единый стандарт периодических задач: платёжные уведомления становятся celery-beat
задачами. celery не шлёт в Telegram → задачи пишут в `outbound_messages`, бот их
отправляет. Строки `periodic_tasks` для 4 задач заводятся здесь дата-миграцией
(идемпотентно, ON CONFLICT DO NOTHING): на чистой БД создаются, на проде/при
восстановлении — не дублируются. Крон считается в зоне парка (celery timezone).

Revision ID: 0022_outbox_and_task_rows
Revises: 0021_schedule_notice_marks
Create Date: 2026-10-10

NB: id ревизии держим ≤32 символов — `alembic_version.version_num` это varchar(32)
(PostgreSQL обрезает/падает; sqlite длину не проверяет, поэтому локально не ловится).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0022_outbox_and_task_rows"
down_revision: Union[str, None] = "0021_schedule_notice_marks"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# name → (task, crontab) в зоне парка.
_TASK_ROWS = [
    ("Утреннее напоминание о платеже", "app.tasks.notifications.notify_daily_reminders", "0 9 * * *"),
    ("Предупреждение за 15 мин до срока", "app.tasks.notifications.notify_overdue_warning", "45 21 * * *"),
    ("Блокировка включена (22:00)", "app.tasks.notifications.notify_block_notice", "0 22 * * *"),
    ("Админ-дайджест (22:05)", "app.tasks.notifications.notify_admin_digest", "5 22 * * *"),
]


def upgrade() -> None:
    op.create_table(
        "outbound_messages",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("recipient_tg_user_id", sa.BigInteger(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        # Отдельно от sent_at: «сдались» (постоянная ошибка/лимит) ≠ «доставлено».
        sa.Column("failed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_outbound_messages_recipient_tg_user_id",
        "outbound_messages",
        ["recipient_tg_user_id"],
    )
    op.create_index("ix_outbound_messages_created_at", "outbound_messages", ["created_at"])

    bind = op.get_bind()
    for name, task, crontab in _TASK_ROWS:
        bind.execute(
            sa.text(
                "INSERT INTO periodic_tasks "
                "(name, task, interval_seconds, crontab, args, enabled, "
                " last_run_at, total_run_count, created_at, updated_at) "
                "VALUES (:name, :task, NULL, :crontab, NULL, true, "
                " CURRENT_TIMESTAMP, 0, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            {"name": name, "task": task, "crontab": crontab},
        )


def downgrade() -> None:
    bind = op.get_bind()
    for name, _task, _crontab in _TASK_ROWS:
        bind.execute(
            sa.text("DELETE FROM periodic_tasks WHERE name = :name"), {"name": name}
        )
    op.drop_index("ix_outbound_messages_created_at", table_name="outbound_messages")
    op.drop_index(
        "ix_outbound_messages_recipient_tg_user_id", table_name="outbound_messages"
    )
    op.drop_table("outbound_messages")
