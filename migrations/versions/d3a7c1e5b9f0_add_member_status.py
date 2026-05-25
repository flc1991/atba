"""Add status column to members and backfill from membership_year

Revision ID: d3a7c1e5b9f0
Revises: b8f1c4e9d273
Create Date: 2026-05-25

"""
from datetime import date
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'd3a7c1e5b9f0'
down_revision: Union[str, None] = 'b8f1c4e9d273'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('members') as batch:
        batch.add_column(
            sa.Column(
                'status', sa.String(20),
                nullable=False, server_default='member',
            )
        )

    # Backfill: rows whose membership_year is already past the current calendar
    # year are flipped to "expired"; everything else stays at the default
    # server-side "member".
    current_year = date.today().year
    op.execute(
        sa.text(
            "UPDATE members SET status = 'expired' WHERE membership_year < :year"
        ).bindparams(year=current_year)
    )


def downgrade() -> None:
    with op.batch_alter_table('members') as batch:
        batch.drop_column('status')
