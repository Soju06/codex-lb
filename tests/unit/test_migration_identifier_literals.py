from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.core.auth.dashboard_access import PRESET_ROLE_IDS, PresetRoleSlug
from app.db.models import COMPAT_ADMIN_USER_ID, COMPAT_ADMIN_USERNAME

pytestmark = pytest.mark.unit

#: Every revision that has to name the bootstrap account. They freeze the ids
#: as literals so the revision keeps working after the module that defined them
#: moves or goes -- which is exactly what happened to ``dashboard_users.compat``.
_MIGRATIONS = (
    Path("app/db/alembic/versions/20260909_010000_add_dashboard_users.py"),
    Path("app/db/alembic/versions/20260909_020000_reproject_compat_admin_credentials.py"),
    Path("app/db/alembic/versions/20260912_010000_drop_legacy_dashboard_credentials.py"),
)

_RUNTIME_IDENTIFIERS = {
    "COMPAT_ADMIN_USER_ID": COMPAT_ADMIN_USER_ID,
    "COMPAT_ADMIN_USERNAME": COMPAT_ADMIN_USERNAME,
    "ADMIN_ROLE_ID": PRESET_ROLE_IDS[PresetRoleSlug.ADMIN],
}


@pytest.mark.parametrize("migration", _MIGRATIONS, ids=[path.stem for path in _MIGRATIONS])
def test_migration_literals_match_the_runtime_identifiers(migration: Path) -> None:
    """The revisions freeze the ids instead of importing an application module."""

    spec = importlib.util.spec_from_file_location(migration.stem, migration)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    frozen = {name: getattr(module, name) for name in _RUNTIME_IDENTIFIERS if hasattr(module, name)}
    assert frozen, f"{migration.stem} names no frozen identifier"
    for name, value in frozen.items():
        assert value == _RUNTIME_IDENTIFIERS[name], name
    source = migration.read_text()
    assert "from app.modules" not in source and "from app.core" not in source
    assert "from app.db" not in source
