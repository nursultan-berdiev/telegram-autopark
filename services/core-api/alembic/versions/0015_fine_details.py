"""Подробности штрафа, срок скидки от вручения и следы синхронизации.

Статья, название нарушения, место и код оплаты до сих пор склеивались в одну
строку `note` — по ней нельзя ни собрать карточку, ни заплатить. Разносим по
полям; `note` остаётся свободным текстом carcheck, ручного ввода и
нераспознанной записи.

Срок скидки идёт от ВРУЧЕНИЯ постановления, а не от нарушения: сервис 06.09
отдавал «30 дней» по нарушению от 30.08 именно потому, что `deliveryDate`
пуст — отсчёт не начался. Поэтому храним дату вручения и посчитанный от неё
предельный день скидки.

`last_seen_*` и `paid_by` появляются ради синхронизации: оплаченный штраф
пропадает из ответа сервиса, и закрывать его по факту исчезновения можно
только зная, когда и каким источником он был виден в последний раз.

Старые штрафы не переразбираем: поля заполнит ближайший прогон — синхронизация
теперь обновляет и уже заведённые записи.

Revision ID: 0015_fine_details
Revises: 0014_fine_discount
Create Date: 2026-09-06
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0015_fine_details"
down_revision: Union[str, None] = "0014_fine_discount"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("fines", sa.Column("article", sa.String(64), nullable=True))
    op.add_column("fines", sa.Column("violation_title", sa.Text(), nullable=True))
    op.add_column("fines", sa.Column("place", sa.Text(), nullable=True))
    op.add_column("fines", sa.Column("payment_code", sa.String(32), nullable=True))
    # bg (автофиксация) | erpn (электронный реестр) | afp (тип carcheck).
    # Живого образца ERPN не было ни разу — по этому полю мы узнаем, что он
    # появился, не перечитывая логи.
    op.add_column("fines", sa.Column("protocol_kind", sa.String(16), nullable=True))
    # Date, а не DateTime: это юридический срок в днях, и время суток в нём
    # означало бы сдвиг на сутки при первой же смене часового пояса.
    op.add_column("fines", sa.Column("delivery_date", sa.Date(), nullable=True))
    op.add_column("fines", sa.Column("discount_until", sa.Date(), nullable=True))
    op.add_column(
        "fines", sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column("fines", sa.Column("last_seen_source", sa.String(32), nullable=True))
    # admin — отметил человек, tolom — штраф пропал из ответа сервиса.
    op.add_column("fines", sa.Column("paid_by", sa.String(16), nullable=True))
    # Прогон, запущенный кнопкой из бота, заводится до постановки в очередь —
    # иначе свой прогон не отличить от кронового, начавшегося в ту же секунду.
    op.add_column("task_runs", sa.Column("requested_by", sa.BigInteger(), nullable=True))
    _create_status_index()


def _create_status_index() -> None:
    """На PostgreSQL строим CONCURRENTLY: обычный CREATE INDEX блокирует запись
    в `fines`, а синхронизация штрафов идёт по расписанию и может совпасть с
    выкатом. Вне транзакции — иначе CONCURRENTLY не разрешён."""
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        op.create_index("ix_fines_status_issued", "fines", ["status", "issued_at"])
        return
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_fines_status_issued",
            "fines",
            ["status", "issued_at"],
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    op.drop_index("ix_fines_status_issued", table_name="fines")
    op.drop_column("task_runs", "requested_by")
    for column in (
        "paid_by",
        "last_seen_source",
        "last_seen_at",
        "discount_until",
        "delivery_date",
        "protocol_kind",
        "payment_code",
        "place",
        "violation_title",
        "article",
    ):
        op.drop_column("fines", column)
