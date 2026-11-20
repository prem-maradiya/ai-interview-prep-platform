"""discussions, editorials, contests and whiteboards

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-20 18:00:00
"""
from alembic import op
import sqlalchemy as sa


revision = '0011'
down_revision = '0010'
branch_labels = None
depends_on = None

TENANT = "current_setting('app.tenant_id', true)"
SYSTEM = "coalesce(current_setting('app.rls_system', true), '') = 'on'"


def _ts(name, nullable=False):
    return sa.Column(name, sa.DateTime(timezone=True), nullable=nullable)


def upgrade() -> None:
    op.create_table('contests',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('created_by', sa.String(length=36), nullable=True),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=False),
    sa.Column('problem_ids', sa.JSON(), nullable=False),
    _ts('starts_at'), _ts('ends_at'),
    sa.Column('freeze_minutes', sa.Integer(), nullable=False),
    sa.Column('penalty_minutes', sa.Integer(), nullable=False),
    _ts('created_at'),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('contests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_contests_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_contests_starts_at'), ['starts_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_contests_ends_at'), ['ends_at'], unique=False)

    op.create_table('discussions',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('problem_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('parent_id', sa.String(length=36), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('score', sa.Integer(), nullable=False),
    _ts('created_at'), _ts('edited_at', True), _ts('deleted_at', True),
    sa.ForeignKeyConstraint(['parent_id'], ['discussions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('discussions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_discussions_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_discussions_parent_id'), ['parent_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_discussions_problem_id'), ['problem_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_discussions_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('discussion_votes',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('discussion_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('value', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['discussion_id'], ['discussions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('discussion_id', 'user_id')
    )
    with op.batch_alter_table('discussion_votes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_discussion_votes_discussion_id'), ['discussion_id'], unique=False)

    op.create_table('editorials',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('problem_id', sa.String(length=36), nullable=False),
    sa.Column('author_id', sa.String(length=36), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    _ts('created_at'), _ts('updated_at'),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('tenant_id', 'problem_id')
    )
    with op.batch_alter_table('editorials', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_editorials_problem_id'), ['problem_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_editorials_tenant_id'), ['tenant_id'], unique=False)

    op.create_table('problem_reveals',
    sa.Column('id', sa.String(length=36), nullable=False),
    sa.Column('tenant_id', sa.String(length=36), nullable=False),
    sa.Column('user_id', sa.String(length=36), nullable=False),
    sa.Column('problem_id', sa.String(length=36), nullable=False),
    _ts('created_at'),
    sa.ForeignKeyConstraint(['problem_id'], ['problems.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['tenant_id'], ['tenants.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'problem_id')
    )
    with op.batch_alter_table('problem_reveals', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_problem_reveals_tenant_id'), ['tenant_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_problem_reveals_user_id'), ['user_id'], unique=False)

    with op.batch_alter_table('submissions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('contest_id', sa.String(length=36), nullable=True))
        batch_op.create_index(batch_op.f('ix_submissions_contest_id'), ['contest_id'], unique=False)
        batch_op.create_foreign_key('fk_submissions_contest_id_contests', 'contests', ['contest_id'], ['id'],
                                    ondelete='SET NULL')
    with op.batch_alter_table('interviews', schema=None) as batch_op:
        batch_op.add_column(sa.Column('board', sa.JSON(), nullable=True))
    with op.batch_alter_table('interview_messages', schema=None) as batch_op:
        batch_op.add_column(sa.Column('board_text', sa.Text(), nullable=True))
    with op.batch_alter_table('peer_sessions', schema=None) as batch_op:
        batch_op.add_column(sa.Column('board', sa.JSON(), nullable=False, server_default=sa.text("'{}'")))

    if op.get_bind().dialect.name == "postgresql":
        tables = ("contests", "discussions", "discussion_votes", "editorials", "problem_reveals")
        for t in tables:
            op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
            op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")
        for t in ("contests", "discussions", "editorials", "problem_reveals"):
            op.execute(f"CREATE POLICY tenant_isolation ON {t} USING ({SYSTEM} OR tenant_id = {TENANT}) "
                       f"WITH CHECK ({SYSTEM} OR tenant_id = {TENANT})")
        op.execute("CREATE POLICY parent_read ON discussion_votes FOR SELECT "
                   "USING (EXISTS (SELECT 1 FROM discussions d WHERE d.id = discussion_votes.discussion_id))")
        owned = (f"{SYSTEM} OR EXISTS (SELECT 1 FROM discussions d WHERE d.id = discussion_votes.discussion_id "
                 f"AND d.tenant_id = {TENANT})")
        op.execute(f"CREATE POLICY parent_write ON discussion_votes USING ({owned}) WITH CHECK ({owned})")


def downgrade() -> None:
    with op.batch_alter_table('peer_sessions', schema=None) as batch_op:
        batch_op.drop_column('board')
    with op.batch_alter_table('interview_messages', schema=None) as batch_op:
        batch_op.drop_column('board_text')
    with op.batch_alter_table('interviews', schema=None) as batch_op:
        batch_op.drop_column('board')
    with op.batch_alter_table('submissions', schema=None) as batch_op:
        batch_op.drop_constraint('fk_submissions_contest_id_contests', type_='foreignkey')
        batch_op.drop_index(batch_op.f('ix_submissions_contest_id'))
        batch_op.drop_column('contest_id')
    for table, indexes in (
        ('problem_reveals', ['user_id', 'tenant_id']), ('editorials', ['tenant_id', 'problem_id']),
        ('discussion_votes', ['discussion_id']), ('discussions', ['tenant_id', 'problem_id', 'parent_id', 'created_at']),
        ('contests', ['ends_at', 'starts_at', 'tenant_id']),
    ):
        with op.batch_alter_table(table, schema=None) as batch_op:
            for col in indexes:
                batch_op.drop_index(batch_op.f(f'ix_{table}_{col}'))
        op.drop_table(table)
