"""Add payments table

Revision ID: e7a1d4f6b892
Revises: d9f3b5c2a846
Create Date: 2026-11-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'e7a1d4f6b892'
down_revision: Union[str, None] = 'd9f3b5c2a846'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'payments',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('kind', sa.String(20), nullable=False),
        sa.Column('record_id', sa.Integer(), nullable=True),
        sa.Column('method', sa.String(20), nullable=False),
        sa.Column('status', sa.String(20), nullable=False),
        sa.Column('entry_cents', sa.Integer(), nullable=False),
        sa.Column('processing_fee_cents', sa.Integer(), nullable=False),
        sa.Column('total_cents', sa.Integer(), nullable=False),
        sa.Column('received_cents', sa.Integer(), nullable=True),
        sa.Column('received_on', sa.Date(), nullable=True),
        sa.Column('reference', sa.String(255), nullable=True),
        sa.Column('paypal_order_id', sa.String(100), nullable=True, unique=True),
        sa.Column('paypal_capture_id', sa.String(100), nullable=True, unique=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('paid_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('voided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('recorded_by', sa.String(520), nullable=True),
        sa.Column('note', sa.String(500), nullable=True),
    )
    op.create_index('ix_payments_record', 'payments', ['kind', 'record_id'])
    op.create_index(
        'uq_payments_open_check', 'payments', ['kind', 'record_id'], unique=True,
        sqlite_where=sa.text("status = 'awaiting_check'"),
    )


def downgrade() -> None:
    op.drop_table('payments')
