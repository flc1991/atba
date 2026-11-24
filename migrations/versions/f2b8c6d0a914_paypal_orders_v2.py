"""PayPal Orders v2: payment ids, review flag, processed webhook events

Revision ID: f2b8c6d0a914
Revises: e7a1d4f6b892
Create Date: 2026-11-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f2b8c6d0a914'
down_revision: Union[str, None] = 'e7a1d4f6b892'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('payments', sa.Column('public_id', sa.String(64), nullable=True))
    op.add_column(
        'payments',
        sa.Column('needs_review', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index('ix_payments_public_id', 'payments', ['public_id'], unique=True)
    op.create_index(
        'uq_payments_active_paypal', 'payments', ['kind', 'record_id'], unique=True,
        sqlite_where=sa.text("status IN ('capturing', 'pending')"),
    )
    op.create_table(
        'paypal_events',
        sa.Column('event_id', sa.String(100), primary_key=True),
        sa.Column('event_type', sa.String(100), nullable=False),
        sa.Column('received_at', sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('paypal_events')
    op.drop_index('uq_payments_active_paypal', table_name='payments')
    op.drop_index('ix_payments_public_id', table_name='payments')
    op.drop_column('payments', 'needs_review')
    op.drop_column('payments', 'public_id')
