from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.modules.dashboard_users.compat import COMPAT_ADMIN_USER_ID, COMPAT_ADMIN_USERNAME

pytestmark = pytest.mark.unit

_MIGRATIONS = (
    Path("app/db/alembic/versions/20260909_010000_add_dashboard_users.py"),
    Path("app/db/alembic/versions/20260909_020000_reproject_compat_admin_credentials.py"),
)


@pytest.mark.parametrize("migration", _MIGRATIONS, ids=[path.stem for path in _MIGRATIONS])
def test_migration_literals_match_the_runtime_identifiers(migration: Path) -> None:
    """The revisions freeze the ids instead of importing the transient compat module."""

    spec = importlib.util.spec_from_file_location(migration.stem, migration)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.COMPAT_ADMIN_USER_ID == COMPAT_ADMIN_USER_ID
    assert module.COMPAT_ADMIN_USERNAME == COMPAT_ADMIN_USERNAME
    assert module.ADMIN_ROLE_ID == PRESET_ROLE_IDS[PresetRoleSlug.ADMIN]
    source = migration.read_text()
    assert "from app.modules" not in source and "from app.core" not in source
