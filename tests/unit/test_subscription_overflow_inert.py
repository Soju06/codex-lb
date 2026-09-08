"""Ratchet: the subscription-overflow designation is inert on the request path (#2123 WP-B).

Design invariant I9 (zero cost when disabled) is proven mechanically in this
stage: nothing under ``app/modules/proxy`` or ``app/core`` -- the request hot
path, the clients, the settings cache and the model registry -- may mention the
designation, the pin table, or the dashboard-only helper module. The setting
therefore adds no read, lock, or per-event work to any request: the two new
columns ride on the ``dashboard_settings`` row the hot path's single
``SettingsCache.get()`` already loads, and no code reads them there.

WP-C1/WP-C2 introduce the pin repository and the overflow decision and are
expected to relax this test deliberately, together with the spec deltas that
make overflow routing reachable.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app"

# Identifiers the request path must not know about in this stage.
_FORBIDDEN_IDENTIFIERS = (
    "subscription_overflow",
    "ModelSourcePin",
    "model_source_pins",
    "PIN_IDLE_TTL",
    "PIN_TOMBSTONE_GRACE",
    "DRAIN_WINDOW",
)
_FORBIDDEN_PATTERN = re.compile("|".join(re.escape(identifier) for identifier in _FORBIDDEN_IDENTIFIERS))

# Request-path and core packages: the hot path proper plus everything it imports.
_REQUEST_PATH_DIRS = (
    APP_DIR / "modules" / "proxy",
    APP_DIR / "core",
    APP_DIR / "modules" / "api_keys",
)

# The only production files allowed to mention the designation or the pin table.
_ALLOWED_READERS = frozenset(
    {
        "app/db/models.py",
        "app/modules/settings/api.py",
        "app/modules/settings/repository.py",
        "app/modules/settings/schemas.py",
        "app/modules/settings/service.py",
        "app/modules/settings/subscription_overflow.py",
        "app/modules/model_sources/api.py",
    }
)
_ALLOWED_PREFIXES = ("app/db/alembic/versions/",)


def _mentions(path: Path) -> list[str]:
    hits: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if _FORBIDDEN_PATTERN.search(line):
            hits.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line_number}: {line.strip()}")
    return hits


def test_request_path_and_core_never_mention_the_overflow_designation() -> None:
    hits: list[str] = []
    for directory in _REQUEST_PATH_DIRS:
        assert directory.is_dir(), directory
        for path in sorted(directory.rglob("*.py")):
            hits.extend(_mentions(path))
    assert hits == [], (
        "The subscription-overflow designation must stay inert on the request path until WP-C2 wires it "
        "(relax this ratchet together with the routing spec deltas):\n" + "\n".join(hits)
    )


def test_only_the_settings_and_model_source_dashboard_modules_read_the_designation() -> None:
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in _ALLOWED_READERS or relative.startswith(_ALLOWED_PREFIXES):
            continue
        offenders.extend(_mentions(path))
    assert offenders == [], "Unexpected reader of the subscription-overflow designation:\n" + "\n".join(offenders)


def test_allowed_readers_exist_so_the_allowlist_cannot_rot() -> None:
    missing = [relative for relative in sorted(_ALLOWED_READERS) if not (REPO_ROOT / relative).is_file()]
    assert missing == []
    assert any((REPO_ROOT / "app/db/alembic/versions").glob("*_add_subscription_overflow.py"))


def test_dashboard_helper_module_is_not_imported_outside_the_dashboard_modules() -> None:
    importers: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in _ALLOWED_READERS:
            continue
        if "app.modules.settings.subscription_overflow" in path.read_text(encoding="utf-8"):
            importers.append(relative)
    assert importers == []
