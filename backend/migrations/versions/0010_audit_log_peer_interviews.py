"""audit log and peer mock interviews

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-20 15:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0010'
down_revision = '0009'
branch_labels = None
depends_on = None

TENANT = "current_setting('app.tenant_id', true)"
SYSTEM = "coalesce(current_setting('app.rls_system', true), '') = 'on'"


def upgrade() -> None:
    op.create_table('audit_events',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('actor_id', sa.String(length=36), nullable=True),
    sa.Column('actor_name', sa.String(length=120), nullable=False),
    sa.Column('actor_email', sa.String(length=255), nullable=False),
    sa.Column('action', sa.String(length=40), nullable=False),
    sa.Column('target_type', sa.String(length=30), nullable=False),
    sa.Column('target_id', sa.String(length=36), nullable=False),
    sa.Column('target_label', sa.String(length=200), nullable=False),
    sa.Column('details', sa.JSON(), nullable=False),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_events_action'), ['action'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_events_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_events_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('peer_sessions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('host_id', sa.String(length=36), nullable=False),
    sa.Column('guest_id', sa.String(length=36), nullable=True),
    sa.Column('invited_id', sa.String(length=36), nullable=True),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('host_first', sa.Boolean(), nullable=False),
    sa.Column('round', sa.Integer(), nullable=False),
    sa.Column('problem_ids', sa.JSON(), nullable=False),
    sa.Column('code', sa.JSON(), nullable=False),
    sa.Column('notes', sa.JSON(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.ForeignKeyConstraint(['guest_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['host_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('peer_sessions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_peer_sessions_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_peer_sessions_guest_id'), ['guest_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_peer_sessions_host_id'), ['host_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_peer_sessions_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_peer_sessions_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('peer_feedback',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('session_id', sa.String(length=36), nullable=False),
    sa.Column('round', sa.Integer(), nullable=False),
    sa.Column('from_id', sa.String(length=36), nullable=False),
    sa.Column('to_id', sa.String(length=36), nullable=False),
    sa.Column('problem_solving', sa.Integer(), nullable=False),
    sa.Column('communication', sa.Integer(), nullable=False),
    sa.Column('code_quality', sa.Integer(), nullable=False),
    sa.Column('hire', sa.String(length=12), nullable=False),
    sa.Column('comments', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['from_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id'], ['peer_sessions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['to_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('session_id', 'round')
    )
    with op.batch_alter_table('peer_feedback', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_peer_feedback_session_id'), ['session_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_peer_feedback_to_id'), ['to_id'], unique=False)

    op.create_table('peer_messages',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('session_id', sa.String(length=36), nullable=False),
    sa.Column('sender_id', sa.String(length=36), nullable=False),
    sa.Column('round', sa.Integer(), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['session_id'], ['peer_sessions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('peer_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_peer_messages_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_peer_messages_session_id'), ['session_id'], unique=False)

    if op.get_bind().dialect.name == "postgresql":
        # Same row-level security as the other tenant tables (see 0008). The app role gets access to
        # these tables through the default privileges set up there.
        for t in ("audit_events", "peer_sessions", "peer_feedback", "peer_messages"):
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        for t in ("audit_events", "peer_sessions"):
            op.execute(f"CREATE POLICY tenant_isolation ON {t} USING ({SYSTEM} OR tenant_id = {TENANT}) "
                       f"WITH CHECK ({SYSTEM} OR tenant_id = {TENANT})")
        for t in ("peer_feedback", "peer_messages"):
            op.execute(f"CREATE POLICY parent_read ON {t} FOR SELECT "
                       f"USING (EXISTS (SELECT 1 FROM peer_sessions p WHERE p.id = {t}.session_id))")
            owned = (f"{SYSTEM} OR EXISTS (SELECT 1 FROM peer_sessions p WHERE p.id = {t}.session_id "
                     f"AND p.tenant_id = {TENANT})")
            op.execute(f"CREATE POLICY parent_write ON {t} USING ({owned}) WITH CHECK ({owned})")


def downgrade() -> None:
    with op.batch_alter_table('peer_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_peer_messages_session_id'))
        batch_op.drop_index(batch_op.f('ix_peer_messages_created_at'))
    op.drop_table('peer_messages')
    with op.batch_alter_table('peer_feedback', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_peer_feedback_to_id'))
        batch_op.drop_index(batch_op.f('ix_peer_feedback_session_id'))
    op.drop_table('peer_feedback')
    with op.batch_alter_table('peer_sessions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_peer_sessions_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_peer_sessions_status'))
        batch_op.drop_index(batch_op.f('ix_peer_sessions_host_id'))
        batch_op.drop_index(batch_op.f('ix_peer_sessions_guest_id'))
        batch_op.drop_index(batch_op.f('ix_peer_sessions_created_at'))
    op.drop_table('peer_sessions')
    with op.batch_alter_table('audit_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_events_tenant_id'))
        batch_op.drop_index(batch_op.f('ix_audit_events_created_at'))
        batch_op.drop_index(batch_op.f('ix_audit_events_action'))
    op.drop_table('audit_events')
