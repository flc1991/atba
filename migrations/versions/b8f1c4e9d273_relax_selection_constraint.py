"""Allow same (entry, trial_event) row twice for two-trial weekends

The uniqueness key now includes akc_trial_pref so a single entry can register
the same event in both Event 1 and Event 2.

Revision ID: b8f1c4e9d273
Revises: a4d6f8e2c3b9
Create Date: 2026-05-23

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = 'b8f1c4e9d273'
down_revision: Union[str, None] = 'a4d6f8e2c3b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('trial_entry_selections') as batch:
        batch.drop_constraint('uq_entry_event', type_='unique')
        batch.create_unique_constraint(
            'uq_entry_event_pref',
            ['trial_entry_id', 'trial_event_id', 'akc_trial_pref'],
        )


def downgrade() -> None:
    with op.batch_alter_table('trial_entry_selections') as batch:
        batch.drop_constraint('uq_entry_event_pref', type_='unique')
        batch.create_unique_constraint(
            'uq_entry_event',
            ['trial_entry_id', 'trial_event_id'],
        )
