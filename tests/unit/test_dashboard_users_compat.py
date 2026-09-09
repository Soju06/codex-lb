from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.modules.dashboard_users.compat import COMPAT_ADMIN_USER_ID, COMPAT_ADMIN_USERNAME

pytestmark = pytest.mark.unit

_MIGRATION = Path("app/db/alembic/versions/20260909_010000_add_dashboard_users.py")


def test_migration_literals_match_the_runtime_identifiers() -> None:
    """The revision freezes the ids instead of importing the transient compat module."""

    spec = importlib.util.spec_from_file_location("add_dashboard_users_revision", _MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.COMPAT_ADMIN_USER_ID == COMPAT_ADMIN_USER_ID
    assert module.COMPAT_ADMIN_USERNAME == COMPAT_ADMIN_USERNAME
    assert module.ADMIN_ROLE_ID == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
