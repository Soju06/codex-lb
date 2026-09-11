"""Re-project the legacy dashboard credential onto the compat ``admin`` user once more.

Revision ID: 20260909_020000_reproject_compat_admin_credentials
Revises: 20260909_010000_add_dashboard_users
Create Date: 2026-09-09

Through the previous release the legacy ``dashboard_settings`` columns were the
authoritative credential and the ``admin`` user row a mirror; a replica still
running that release during a rolling upgrade may have changed the password or
TOTP secret on the legacy row only. This data-only revision copies the legacy
credential onto the user row one last time before the user row becomes the
source of truth (and the legacy columns become the mirror). Downgrade is a
no-op: nothing structural changes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260909_020000_reproject_compat_admin_credentials"
down_revision = "20260909_010000_add_dashboard_users"
branch_labels = None
depends_on = None

# Frozen copies of the runtime identifiers (see 20260909_010000): a migration
# must not import transient application modules. The preset role rows are
# guaranteed by 20260909_000000 earlier in this chain.
COMPAT_ADMIN_USER_ID = "7a4fc02d-216e-5be7-b974-bc437f23df60"
COMPAT_ADMIN_USERNAME = "admin"
ADMIN_ROLE_ID = "3fe7dc57-aabd-5b16-9850-b6d464087f07"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    if not inspector.has_table("dashboard_settings") or not inspector.has_table("dashboard_users"):
        return

    settings_row = bind.execute(
        sa.text(
            "SELECT password_hash, totp_secret_encrypted, totp_last_verified_step FROM dashboard_settings WHERE id = 1"
        )
    ).first()
    if settings_row is None:
        return
    legacy_password_hash, legacy_totp_secret, legacy_totp_step = settings_row

    existing_admin = bind.execute(
        sa.text("SELECT id FROM dashboard_users WHERE id = :id OR username = :username"),
        {"id": COMPAT_ADMIN_USER_ID, "username": COMPAT_ADMIN_USERNAME},
    ).first()

    if legacy_password_hash is None:
        # The previous release removed the password: the compat row (if any)
        # must be credential-less too. The row itself stays so the account
        # keeps its identity and settings.
        if existing_admin is not None:
            bind.execute(
                sa.text(
                    "UPDATE dashboard_users SET password_hash = NULL, totp_secret_encrypted = NULL, "
                    "totp_last_verified_step = NULL WHERE id = :id"
                ),
                {"id": existing_admin[0]},
            )
        return

    credential = {
        "password_hash": legacy_password_hash,
        "totp_secret": legacy_totp_secret,
        "totp_step": legacy_totp_step,
    }
    if existing_admin is not None:
        bind.execute(
            sa.text(
                "UPDATE dashboard_users SET password_hash = :password_hash, totp_secret_encrypted = :totp_secret, "
                "totp_last_verified_step = :totp_step WHERE id = :id"
            ),
            {**credential, "id": existing_admin[0]},
        )
        return

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
            **credential,
            "id": COMPAT_ADMIN_USER_ID,
            "username": COMPAT_ADMIN_USERNAME,
            "role_id": ADMIN_ROLE_ID,
            "false_value": False,
            "true_value": True,
        },
    )


def downgrade() -> None:
    # Data-only re-projection; the previous revision's downgrade drops the tables.
    return
