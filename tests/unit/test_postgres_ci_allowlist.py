"""Guard the PostgreSQL CI allow-list against silently dead PostgreSQL-only tests.

CI runs ``make test-postgres`` against an explicit allow-list
(``POSTGRES_PYTEST_TARGETS`` in the ``Makefile``), while the SQLite pytest
matrix skips every test decorated ``@pytest.mark.skipif(not
_is_postgresql_database_url(...))``. A PostgreSQL-only test that is not
registered in the allow-list therefore never executes anywhere in CI, even
though it passes locally against a PostgreSQL database. This has slipped
through review twice (#1666 registered the usage_history covering-index
repair tests after the fact; the model_source_pins invalid-index repair test
of #2123 WP-A shipped unregistered), so the wiring is pinned here.

Only the decorator form is enforced: it is the convention for the
``tests/integration/test_migrations.py`` and
``tests/integration/test_migration_serialization.py`` families, and the file
path plus function name is enough to derive the pytest node id without
collecting (and thereby importing) the integration suite.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MAKEFILE = REPO_ROOT / "Makefile"
INTEGRATION_TESTS_DIR = REPO_ROOT / "tests" / "integration"
_POSTGRESQL_ONLY_SKIPIF_MARKERS = ("_is_postgresql_database_url(", "PostgreSQL-only")


def _postgres_pytest_targets(makefile_text: str) -> tuple[str, ...]:
    match = re.search(r"^POSTGRES_PYTEST_TARGETS\s*:=\s*(.*?)(?<!\\)\n", makefile_text, re.MULTILINE | re.DOTALL)
    assert match is not None, "POSTGRES_PYTEST_TARGETS is not defined in the Makefile"
    return tuple(token for token in match.group(1).replace("\\\n", " ").split() if token)


def _is_postgresql_only_skipif(decorator: ast.expr, source: str) -> bool:
    if not isinstance(decorator, ast.Call):
        return False
    func = decorator.func
    if not (isinstance(func, ast.Attribute) and func.attr == "skipif"):
        return False
    segment = ast.get_source_segment(source, decorator) or ""
    return any(marker in segment for marker in _POSTGRESQL_ONLY_SKIPIF_MARKERS)


def _postgresql_only_skipif_test_ids() -> tuple[str, ...]:
    node_ids: list[str] = []
    for path in sorted(INTEGRATION_TESTS_DIR.rglob("test_*.py")):
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(ast.parse(source, filename=str(path))):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef) or not node.name.startswith("test_"):
                continue
            if any(_is_postgresql_only_skipif(decorator, source) for decorator in node.decorator_list):
                node_ids.append(f"{relative}::{node.name}")
    return tuple(node_ids)


def test_postgresql_only_skipif_tests_are_registered_in_postgres_pytest_targets() -> None:
    targets = _postgres_pytest_targets(MAKEFILE.read_text(encoding="utf-8"))
    whole_file_targets = {target for target in targets if "::" not in target}
    explicit_targets = {target for target in targets if "::" in target}

    missing = tuple(
        node_id
        for node_id in _postgresql_only_skipif_test_ids()
        if node_id not in explicit_targets and node_id.split("::", 1)[0] not in whole_file_targets
    )

    assert not missing, (
        "PostgreSQL-only tests missing from POSTGRES_PYTEST_TARGETS in the Makefile "
        "(CI `make test-postgres` runs only that allow-list; the SQLite matrix skips them):\n  " + "\n  ".join(missing)
    )


def test_postgresql_only_skipif_detection_sees_the_known_migration_tests() -> None:
    node_ids = set(_postgresql_only_skipif_test_ids())

    assert "tests/integration/test_migrations.py::test_postgresql_upgrade_head_from_empty_database" in node_ids
    assert (
        "tests/integration/test_migration_serialization.py::"
        "test_postgresql_run_upgrade_times_out_when_advisory_lock_is_held" in node_ids
    )
    assert (
        "tests/integration/test_migrations.py::"
        "test_model_source_pins_index_migration_repairs_invalid_leftover_postgresql" in node_ids
    )
