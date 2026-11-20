"""row-level security: tenant isolation enforced by PostgreSQL

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-20 11:00:00

Defense in depth on top of the app's own `tenant_id` filters: even a query that forgets the filter
can't return another tenant's rows. The app sets two transaction-local settings (see
app/db/session.py):

  app.tenant_id   the tenant of the signed-in user
  app.rls_system  'on' only for sign-in, sign-up, seeding and background jobs

Superusers bypass RLS, so the app switches each transaction to the non-superuser role created here
(DB_APP_ROLE, default prepforge_app). SQLite has no RLS: this migration does nothing there.
"""
import re

from alembic import op

from app.core.config import get_settings

revision = '0008'
down_revision = '0007'
branch_labels = None
depends_on = None

TENANT = "current_setting('app.tenant_id', true)"
SYSTEM = "coalesce(current_setting('app.rls_system', true), '') = 'on'"

# Tables with their own tenant_id column.
DIRECT = ["users", "oauth_identities", "invites", "submissions", "interviews", "documents", "review_items",
          "assignments", "comments", "notifications", "user_badges", "usage_events"]
# tenant_id NULL = shared with every tenant (read-only for tenants).
SHARED = ["problems", "problem_lists"]
# Tables without tenant_id: a row is visible when its parent row is.
CHILD = {"test_cases": ("problem_id", "problems"), "interview_messages": ("interview_id", "interviews"),
         "assignment_members": ("assignment_id", "assignments"), "refresh_tokens": ("user_id", "users")}
ALL_TABLES = ["tenants", *DIRECT, *SHARED, *CHILD]


def _role() -> str | None:
    role = get_settings().db_app_role or None
    if role and not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", role):
        raise RuntimeError(f"DB_APP_ROLE {role!r} is not a valid role name")
    return role


def upgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    role = _role()
    if role:
        # Creating a role needs CREATEROLE. Without it, connect the app as a non-superuser instead.
        op.execute(f"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                CREATE ROLE {role} NOLOGIN;
            END IF;
            EXECUTE format('GRANT {role} TO %I', current_user);
        EXCEPTION WHEN insufficient_privilege THEN
            RAISE NOTICE 'Could not create or grant role {role}: connect the app as a non-superuser role';
        END $$;
        """)
        op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE format('GRANT USAGE ON SCHEMA %I TO {role}', current_schema());
                EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA %I TO {role}', current_schema());
                EXECUTE format('GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA %I TO {role}', current_schema());
                EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {role}',
                               current_schema());
            END IF;
        END $$;
        """)

    for t in ALL_TABLES:
        # FORCE: the table owner is subject to the policies too (only superusers bypass them).
        op.execute(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} FORCE ROW LEVEL SECURITY")

    op.execute(f"CREATE POLICY tenant_isolation ON tenants USING ({SYSTEM} OR id = {TENANT}) "
               f"WITH CHECK ({SYSTEM} OR id = {TENANT})")
    for t in DIRECT + SHARED:
        op.execute(f"CREATE POLICY tenant_isolation ON {t} USING ({SYSTEM} OR tenant_id = {TENANT}) "
                   f"WITH CHECK ({SYSTEM} OR tenant_id = {TENANT})")
    for t in SHARED:
        op.execute(f"CREATE POLICY shared_read ON {t} FOR SELECT USING (tenant_id IS NULL)")
    for t, (fk, parent) in CHILD.items():
        # Read: whenever the parent is visible (e.g. test cases of global problems).
        # Write: only under a parent owned by this tenant.
        op.execute(f"CREATE POLICY parent_read ON {t} FOR SELECT "
                   f"USING (EXISTS (SELECT 1 FROM {parent} p WHERE p.id = {t}.{fk}))")
        owned = f"{SYSTEM} OR EXISTS (SELECT 1 FROM {parent} p WHERE p.id = {t}.{fk} AND p.tenant_id = {TENANT})"
        op.execute(f"CREATE POLICY parent_write ON {t} USING ({owned}) WITH CHECK ({owned})")


def downgrade() -> None:
    if op.get_bind().dialect.name != "postgresql":
        return
    for t in ALL_TABLES:
        for policy in ("tenant_isolation", "shared_read", "parent_read", "parent_write"):
            op.execute(f"DROP POLICY IF EXISTS {policy} ON {t}")
        op.execute(f"ALTER TABLE {t} NO FORCE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {t} DISABLE ROW LEVEL SECURITY")
    role = _role()
    if role:
        # The role itself is cluster-wide (other databases may use it), so only this schema's grants go.
        op.execute(f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                EXECUTE format('ALTER DEFAULT PRIVILEGES IN SCHEMA %I REVOKE ALL ON TABLES FROM {role}', current_schema());
                EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA %I FROM {role}', current_schema());
                EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA %I FROM {role}', current_schema());
                EXECUTE format('REVOKE USAGE ON SCHEMA %I FROM {role}', current_schema());
            END IF;
        END $$;
        """)
