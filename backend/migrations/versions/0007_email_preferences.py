"""email preferences and digest tracking

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-20 10:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0007'
down_revision = '0006'
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.add_column(sa.Column('email_prefs', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))
        batch_op.add_column(sa.Column('last_digest_at', sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_column('last_digest_at')
        batch_op.drop_column('email_prefs')
