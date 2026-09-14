from __future__ import annotations

import ast
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

#: Packages a revision must not reach into. Application code moves and is
#: deleted; a revision that imports it stops being replayable the moment it
#: does, and the failure lands on somebody upgrading an old database.
_FORBIDDEN_PACKAGES = ("app.modules", "app.core", "app.db")


def _imported_modules(source: str, filename: str) -> set[str]:
    """Every module name the source imports, by either statement form.

    Substring matching over the text sees ``from app.db import x`` and misses
    ``import app.db.models`` and ``import app.core.auth as auth``, which reach
    the same code just as effectively.
    """

    tree = ast.parse(source, filename=filename)
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.add(node.module)
    return modules


def _forbidden_imports(source: str, filename: str) -> set[str]:
    return {
        module
        for module in _imported_modules(source, filename)
        for package in _FORBIDDEN_PACKAGES
        if module == package or module.startswith(f"{package}.")
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
    assert _forbidden_imports(migration.read_text(), str(migration)) == set()


@pytest.mark.parametrize(
    "source",
    [
        "import app.db.models",
        "import app.core.auth.dashboard_access as access",
        "import sqlalchemy, app.modules.dashboard_users.repository",
        "from app.db import models",
        "from app.core.auth.dashboard_access import PRESET_ROLE_IDS",
        "def upgrade():\n    import app.db.models\n",
    ],
    ids=[
        "plain-import",
        "aliased-import",
        "multi-alias-import",
        "from-import",
        "from-import-deep",
        "function-scoped-import",
    ],
)
def test_every_application_import_form_is_rejected(source: str) -> None:
    """The check reads the import graph, not the text.

    ``from app.db ...`` is the only form a substring search catches; each of the
    others reaches the same mutable application code and used to pass.
    """

    assert _forbidden_imports(source, "<probe>") != set()


@pytest.mark.parametrize(
    "source",
    [
        "import sqlalchemy as sa",
        "from alembic import op",
        "from __future__ import annotations",
        "from . import sibling",
    ],
)
def test_unrelated_imports_are_accepted(source: str) -> None:
    assert _forbidden_imports(source, "<probe>") == set()
