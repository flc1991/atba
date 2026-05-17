"""Trial model expansion: per-event fees, day preference, AKC/AHBA-specific entry fields

Revision ID: 8a4f2c1d9e3b
Revises: 6c3892d688b3
Create Date: 2026-03-29

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = '8a4f2c1d9e3b'
down_revision: Union[str, None] = '6c3892d688b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- trials: add second AKC event number, drop legacy per-class fee ---
    with op.batch_alter_table('trials') as batch_op:
        batch_op.add_column(sa.Column('akc_event_number_2', sa.String(50), nullable=True))
        batch_op.drop_column('fee_per_class_cents')

    # --- trial_events: per-event fee, day availability, test-class flag ---
    with op.batch_alter_table('trial_events') as batch_op:
        batch_op.add_column(
            sa.Column('fee_cents', sa.Integer(), nullable=False, server_default='6000')
        )
        batch_op.add_column(sa.Column('available_days', sa.String(20), nullable=True))
        batch_op.add_column(
            sa.Column('is_test_class', sa.Boolean(), nullable=False, server_default='0')
        )

    # --- trial_entries: governing body, dog details, AKC/AHBA-specific fields, signature ---
    with op.batch_alter_table('trial_entries') as batch_op:
        batch_op.add_column(
            sa.Column('governing_body', sa.String(10), nullable=False, server_default='AKC')
        )
        batch_op.add_column(sa.Column('dog_call_name', sa.String(100), nullable=True))
        batch_op.add_column(sa.Column('dog_sex', sa.String(1), nullable=True))
        batch_op.add_column(sa.Column('dog_dob', sa.Date(), nullable=True))
        batch_op.add_column(sa.Column('dog_sire', sa.String(255), nullable=True))
        batch_op.add_column(sa.Column('dog_dam', sa.String(255), nullable=True))
        batch_op.add_column(sa.Column('dog_breeder', sa.String(255), nullable=True))
        batch_op.add_column(sa.Column('dog_place_of_birth', sa.String(100), nullable=True))
        batch_op.add_column(sa.Column('akc_number_type', sa.String(20), nullable=True))
        batch_op.add_column(sa.Column('akc_foreign_country', sa.String(100), nullable=True))
        batch_op.add_column(sa.Column('akc_owner_names', sa.String(255), nullable=True))
        batch_op.add_column(sa.Column('akc_owner_address', sa.String(500), nullable=True))
        batch_op.add_column(sa.Column('akc_handler_name', sa.String(255), nullable=True))
        batch_op.add_column(sa.Column('akc_handler_address', sa.String(500), nullable=True))
        batch_op.add_column(
            sa.Column('akc_separate_entries', sa.Boolean(), nullable=False, server_default='0')
        )
        batch_op.add_column(sa.Column('ahba_agent_name', sa.String(255), nullable=True))
        batch_op.add_column(sa.Column('ahba_agent_phone', sa.String(50), nullable=True))
        batch_op.add_column(sa.Column('ahba_agent_email', sa.String(255), nullable=True))
        batch_op.add_column(sa.Column('signature', sa.String(255), nullable=True))

    # --- trial_entry_selections: AKC event preference, day preference, nullable class ---
    with op.batch_alter_table('trial_entry_selections') as batch_op:
        batch_op.add_column(sa.Column('akc_trial_pref', sa.String(10), nullable=True))
        batch_op.add_column(sa.Column('day_preference', sa.String(10), nullable=True))
        batch_op.alter_column('trial_event_class_id', nullable=True)


def downgrade() -> None:
    with op.batch_alter_table('trial_entry_selections') as batch_op:
        batch_op.alter_column('trial_event_class_id', nullable=False)
        batch_op.drop_column('day_preference')
        batch_op.drop_column('akc_trial_pref')

    with op.batch_alter_table('trial_entries') as batch_op:
        batch_op.drop_column('signature')
        batch_op.drop_column('ahba_agent_email')
        batch_op.drop_column('ahba_agent_phone')
        batch_op.drop_column('ahba_agent_name')
        batch_op.drop_column('akc_separate_entries')
        batch_op.drop_column('akc_handler_address')
        batch_op.drop_column('akc_handler_name')
        batch_op.drop_column('akc_owner_address')
        batch_op.drop_column('akc_owner_names')
        batch_op.drop_column('akc_foreign_country')
        batch_op.drop_column('akc_number_type')
        batch_op.drop_column('dog_place_of_birth')
        batch_op.drop_column('dog_breeder')
        batch_op.drop_column('dog_dam')
        batch_op.drop_column('dog_sire')
        batch_op.drop_column('dog_dob')
        batch_op.drop_column('dog_sex')
        batch_op.drop_column('dog_call_name')
        batch_op.drop_column('governing_body')

    with op.batch_alter_table('trial_events') as batch_op:
        batch_op.drop_column('is_test_class')
        batch_op.drop_column('available_days')
        batch_op.drop_column('fee_cents')

    with op.batch_alter_table('trials') as batch_op:
        batch_op.drop_column('akc_event_number_2')
        batch_op.add_column(
            sa.Column('fee_per_class_cents', sa.Integer(), nullable=False, server_default='0')
        )
