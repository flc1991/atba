"""Add payment_reference to trial_entries and registrations

Revision ID: f7c4a8e2d5b3
Revises: e5b1c7a9d2f4
Create Date: 2026-06-06

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'f7c4a8e2d5b3'
down_revision: Union[str, None] = 'e5b1c7a9d2f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('trial_entries') as batch:
        batch.add_column(sa.Column('payment_reference', sa.String(255), nullable=True))
    with op.batch_alter_table('registrations') as batch:
        batch.add_column(sa.Column('payment_reference', sa.String(255), nullable=True))

    # Backfill: copy paypal_order_id into payment_reference so the new column
    # is immediately useful for existing PayPal-paid rows.
    op.execute("UPDATE trial_entries SET payment_reference = paypal_order_id WHERE payment_reference IS NULL AND paypal_order_id IS NOT NULL")
    op.execute("UPDATE registrations  SET payment_reference = paypal_order_id WHERE payment_reference IS NULL AND paypal_order_id IS NOT NULL")


def downgrade() -> None:
    with op.batch_alter_table('registrations') as batch:
        batch.drop_column('payment_reference')
    with op.batch_alter_table('trial_entries') as batch:
        batch.drop_column('payment_reference')
