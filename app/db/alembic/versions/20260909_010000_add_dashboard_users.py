"""Add dashboard_users, dashboard_identities, API key ownership columns, and backfill the compat admin.

Revision ID: 20260909_010000_add_dashboard_users
Revises: 20260909_000000_add_dashboard_roles
Create Date: 2026-09-09
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.modules.dashboard_roles.seed import seed_preset_dashboard_roles
from app.modules.dashboard_users.compat import COMPAT_ADMIN_USER_ID, COMPAT_ADMIN_USERNAME

revision = "20260909_010000_add_dashboard_users"
down_revision = "20260909_000000_add_dashboard_roles"
branch_labels = None
depends_on = None

_API_KEY_COLUMNS = ("owner_user_id", "created_by_user_id", "deactivated_reason")


def _columns(bind: sa.engine.Connection, table: str) -> set[str]:
    inspector = sa.inspect(bind)
    if not inspector.has_table(table):
        return set()
    return {column["name"] for column in inspector.get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    if not inspector.has_table("dashboard_users"):
        op.create_table(
            "dashboard_users",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("username", sa.String(length=64), nullable=False, unique=True),
            sa.Column("display_name", sa.String(length=128), nullable=True),
            sa.Column("email", sa.String(length=320), nullable=True, unique=True),
            sa.Column("role_id", sa.String(), sa.ForeignKey("dashboard_roles.id", ondelete="RESTRICT"), nullable=False),
            sa.Column("role_source", sa.String(length=16), server_default=sa.text("'manual'"), nullable=False),
            sa.Column("status", sa.String(length=16), server_default=sa.text("'active'"), nullable=False),
            sa.Column("password_hash", sa.Text(), nullable=True),
            sa.Column("totp_secret_encrypted", sa.LargeBinary(), nullable=True),
            sa.Column("totp_last_verified_step", sa.Integer(), nullable=True),
            sa.Column("session_generation", sa.Integer(), server_default=sa.text("0"), nullable=False),
            sa.Column("must_change_password", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("is_break_glass", sa.Boolean(), server_default=sa.false(), nullable=False),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column(
                "created_by_user_id",
                sa.String(),
                sa.ForeignKey("dashboard_users.id", ondelete="SET NULL"),
                nullable=True,
            ),
            sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.Column("last_login_at", sa.DateTime(), nullable=True),
        )
    if not inspector.has_table("dashboard_identities"):
        op.create_table(
            "dashboard_identities",
            sa.Column("id", sa.String(), primary_key=True),
            sa.Column("user_id", sa.String(), sa.ForeignKey("dashboard_users.id", ondelete="CASCADE"), nullable=False),
            sa.Column("provider", sa.String(length=32), nullable=False),
            sa.Column("provider_key", sa.String(length=128), nullable=False),
            sa.Column("subject", sa.String(length=512), nullable=False),
            sa.Column("email", sa.String(length=320), nullable=True),
            sa.Column("display_name", sa.String(length=128), nullable=True),
            sa.Column("groups_json", sa.Text(), nullable=True),
            sa.Column("last_seen_at", sa.DateTime(), nullable=True),
            sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("provider", "provider_key", "subject", name="uq_dashboard_identities_subject"),
        )
        op.create_index("idx_dashboard_identities_user_id", "dashboard_identities", ["user_id"])

    existing = _columns(bind, "api_keys")
    missing = [name for name in _API_KEY_COLUMNS if name not in existing]
    if missing:
        with op.batch_alter_table("api_keys") as batch_op:
            if "owner_user_id" in missing:
                batch_op.add_column(sa.Column("owner_user_id", sa.String(), nullable=True))
                batch_op.create_foreign_key(
                    "fk_api_keys_owner_user_id",
                    "dashboard_users",
                    ["owner_user_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
            if "created_by_user_id" in missing:
                batch_op.add_column(sa.Column("created_by_user_id", sa.String(), nullable=True))
                batch_op.create_foreign_key(
                    "fk_api_keys_created_by_user_id",
                    "dashboard_users",
                    ["created_by_user_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
            if "deactivated_reason" in missing:
                batch_op.add_column(sa.Column("deactivated_reason", sa.String(length=32), nullable=True))

    # Preset rows must exist for the FK below; harmless re-seed otherwise.
    seed_preset_dashboard_roles(bind)

    # Backfill: the legacy shared admin password becomes the `admin` user. Runs
    # outside the table guards so a re-run after a partial failure still
    # migrates the credential. Idempotent on the deterministic user id and on
    # the unique username.
    settings_row = bind.execute(
        sa.text(
            "SELECT password_hash, totp_secret_encrypted, totp_last_verified_step FROM dashboard_settings WHERE id = 1"
        )
    ).first()
    if settings_row is not None and settings_row[0] is not None:
        existing_admin = bind.execute(
            sa.text("SELECT id FROM dashboard_users WHERE id = :id OR username = :username"),
            {"id": COMPAT_ADMIN_USER_ID, "username": COMPAT_ADMIN_USERNAME},
        ).first()
        if existing_admin is None:
            bind.execute(
                sa.text(
                    "INSERT INTO dashboard_users "
                    "(id, username, display_name, role_id, role_source, status, password_hash, "
                    "totp_secret_encrypted, totp_last_verified_step, session_generation, "
                    "must_change_password, is_break_glass, created_at, updated_at) "
                    "VALUES (:id, :username, NULL, :role_id, 'manual', 'active', :password_hash, "
                    ":totp_secret, :totp_step, 0, :false_value, :true_value, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
                ),
                {
                    "id": COMPAT_ADMIN_USER_ID,
                    "username": COMPAT_ADMIN_USERNAME,
                    "role_id": PRESET_ROLE_IDS[PresetRoleSlug.ADMIN],
                    "password_hash": settings_row[0],
                    "totp_secret": settings_row[1],
                    "totp_step": settings_row[2],
                    "false_value": False,
                    "true_value": True,
                },
            )


def downgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = _columns(bind, "api_keys")
    if existing & set(_API_KEY_COLUMNS):
        with op.batch_alter_table("api_keys") as batch_op:
            if "owner_user_id" in existing:
                batch_op.drop_constraint("fk_api_keys_owner_user_id", type_="foreignkey")
                batch_op.drop_column("owner_user_id")
            if "created_by_user_id" in existing:
                batch_op.drop_constraint("fk_api_keys_created_by_user_id", type_="foreignkey")
                batch_op.drop_column("created_by_user_id")
            if "deactivated_reason" in existing:
                batch_op.drop_column("deactivated_reason")
    if inspector.has_table("dashboard_identities"):
        op.drop_table("dashboard_identities")
    if inspector.has_table("dashboard_users"):
        op.drop_table("dashboard_users")
