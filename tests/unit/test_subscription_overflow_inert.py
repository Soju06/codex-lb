"""Ratchet: the subscription-overflow designation is inert on the request path (#2123 WP-B).

Design invariant I9 (zero cost when disabled) is proven mechanically in this
stage: nothing under ``app/modules/proxy`` or ``app/core`` -- the request hot
path, the clients, the settings cache and the model registry -- may mention the
designation, the pin table, or the dashboard-only helper module. The setting
therefore adds no read, lock, or per-event work to any request: the two new
columns ride on the ``dashboard_settings`` row the hot path's single
``SettingsCache.get()`` already loads, and no code reads them there.

WP-C1 introduces the pin repository (``app/modules/proxy/model_source_pins.py``,
the only request-path module allowed to name the pin table and import the
dashboard helper's TTL constants) and the dispatch owner that carries a pin
intent (``source_dispatch.py``). Neither is imported by the hot path yet, and
the designation identifier itself stays forbidden everywhere on the request
path until WP-C2 wires the overflow decision together with its spec deltas.
The retention job (``app/core/retention/job.py``) purges tombstoned pin rows
and checks the drain invariant, so it may name the pin table and import the
pin module; it reads the drain deadline through the pin module's helper and
never names the designation itself.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_DIR = REPO_ROOT / "app"

# Identifiers the request path must not know about in this stage.
_DESIGNATION_IDENTIFIERS = ("subscription_overflow",)
_PIN_IDENTIFIERS = (
    "ModelSourcePin",
    "model_source_pins",
    "PIN_IDLE_TTL",
    "PIN_TOMBSTONE_GRACE",
    "DRAIN_WINDOW",
)
_FORBIDDEN_IDENTIFIERS = _DESIGNATION_IDENTIFIERS + _PIN_IDENTIFIERS
_FORBIDDEN_PATTERN = re.compile("|".join(re.escape(identifier) for identifier in _FORBIDDEN_IDENTIFIERS))
_DESIGNATION_PATTERN = re.compile("|".join(re.escape(identifier) for identifier in _DESIGNATION_IDENTIFIERS))

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

# WP-C1 pin primitive: the pins module imports the dashboard helper's TTL
# constants by design and is exempt; the dispatch owner may name the pin
# types it carries, and the retention job may purge the pin table, but
# neither may name the designation itself.
_PIN_MODULE = "app/modules/proxy/model_source_pins.py"
_PIN_READERS = frozenset(
    {
        _PIN_MODULE,
        "app/modules/proxy/source_dispatch.py",
        "app/core/retention/job.py",
    }
)

# The hot path proper must not reach the pin table until WP-C2 arms the primitive.
_HOT_PATH_FILES = (
    APP_DIR / "modules" / "proxy" / "api.py",
    APP_DIR / "modules" / "proxy" / "service.py",
    APP_DIR / "modules" / "proxy" / "load_balancer.py",
)
_HOT_PATH_DIRS = (APP_DIR / "modules" / "proxy" / "_service",)


def _mentions(path: Path, pattern: re.Pattern[str] = _FORBIDDEN_PATTERN) -> list[str]:
    hits: list[str] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if pattern.search(line):
            hits.append(f"{path.relative_to(REPO_ROOT).as_posix()}:{line_number}: {line.strip()}")
    return hits


def _forbidden_pattern_for(relative: str) -> re.Pattern[str] | None:
    if relative == _PIN_MODULE:
        return None
    if relative in _PIN_READERS:
        return _DESIGNATION_PATTERN
    return _FORBIDDEN_PATTERN


def test_request_path_and_core_never_mention_the_overflow_designation() -> None:
    hits: list[str] = []
    for directory in _REQUEST_PATH_DIRS:
        assert directory.is_dir(), directory
        for path in sorted(directory.rglob("*.py")):
            pattern = _forbidden_pattern_for(path.relative_to(REPO_ROOT).as_posix())
            if pattern is not None:
                hits.extend(_mentions(path, pattern))
    assert hits == [], (
        "The subscription-overflow designation must stay inert on the request path until WP-C2 wires it "
        "(relax this ratchet together with the routing spec deltas):\n" + "\n".join(hits)
    )


def test_hot_path_never_imports_the_pin_primitive() -> None:
    paths = list(_HOT_PATH_FILES)
    for directory in _HOT_PATH_DIRS:
        paths.extend(sorted(directory.rglob("*.py")))
    importers = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in paths
        if "model_source_pins" in path.read_text(encoding="utf-8")
    ]
    assert importers == [], "The pin primitive is armed by WP-C2, not before:\n" + "\n".join(importers)


def test_only_the_settings_and_model_source_dashboard_modules_read_the_designation() -> None:
    offenders: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in _ALLOWED_READERS or relative.startswith(_ALLOWED_PREFIXES):
            continue
        pattern = _forbidden_pattern_for(relative)
        if pattern is not None:
            offenders.extend(_mentions(path, pattern))
    assert offenders == [], "Unexpected reader of the subscription-overflow designation:\n" + "\n".join(offenders)


def test_allowed_readers_exist_so_the_allowlist_cannot_rot() -> None:
    missing = [relative for relative in sorted(_ALLOWED_READERS) if not (REPO_ROOT / relative).is_file()]
    assert missing == []
    assert all((REPO_ROOT / relative).is_file() for relative in sorted(_PIN_READERS))
    assert any((REPO_ROOT / "app/db/alembic/versions").glob("*_add_subscription_overflow.py"))


def test_dashboard_helper_module_is_not_imported_outside_the_dashboard_modules() -> None:
    importers: list[str] = []
    for path in sorted(APP_DIR.rglob("*.py")):
        relative = path.relative_to(REPO_ROOT).as_posix()
        if relative in _ALLOWED_READERS or relative == _PIN_MODULE:
            continue
        if "app.modules.settings.subscription_overflow" in path.read_text(encoding="utf-8"):
            importers.append(relative)
    assert importers == []


def test_pin_module_importers_are_the_dispatch_owner_and_the_retention_job() -> None:
    """The pin primitive has exactly two importers in this stage.

    The dispatch owner carries a pin intent (never armed before WP-C2) and the
    retention job purges tombstoned rows and checks the drain invariant; the
    request path (api.py, service.py, load_balancer.py, ``_service/**``, and
    every other proxy module) is armed by WP-C2, not before.
    """
    importers = [
        path.relative_to(REPO_ROOT).as_posix()
        for path in sorted(APP_DIR.rglob("*.py"))
        if path.relative_to(REPO_ROOT).as_posix() != _PIN_MODULE
        and "app.modules.proxy.model_source_pins" in path.read_text(encoding="utf-8")
    ]
    assert importers == ["app/core/retention/job.py", "app/modules/proxy/source_dispatch.py"]
