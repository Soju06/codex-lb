#!/usr/bin/env python3
"""Configuration-tier fitness checks for ``Settings`` (configuration-tiers capability).

Run by ``make lint`` (architecture-check). Needs the app importable, so it runs
under ``uv run`` unlike the stdlib-only simplicity-budgets script.

Checks:

1. Every ``Settings`` field has a tier in ``app.core.config.tiers.SETTING_TIERS``
   (error). Entries for fields that no longer exist only warn, so field
   removals and this map can land in either order.
2. Every T3 field has a ``dashboard_settings`` column of the same name, a
   ``DASHBOARD_HOMES`` mapping to an existing ``table.column``, or a
   ``MIGRATING`` entry (error). A ``DASHBOARD_HOMES`` target that is not
   ``table.column`` or names a column that does not exist is an error. A
   ``MIGRATING`` or ``DASHBOARD_HOMES`` entry that is redundant (the same-name
   column exists, the field is not T3, or the field is gone) only warns.
3. No ``os.environ`` / ``os.getenv`` / ``dotenv_values`` use under ``app/``
   outside ``app/core/config/settings.py``, except the allowlisted files, each
   capped at its recorded number of reading lines (error when a file exceeds
   its cap, so new reads in allowlisted files are caught). An allowlisted file
   with fewer reads than its cap only warns (lower the cap).
4. ``.env.example`` mentions no T2/T3/T4 field (error) — only bootstrap and
   topology settings belong in the operator-facing template.
5. ``len(Settings.model_fields)`` stays within ``[settings_fields].max`` in
   ``.github/simplicity-budgets.toml`` (error; a missing section is a config
   error).

Exit codes: 0 = clean (warnings allowed), 1 = violations, 2 = config error.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = ROOT / "app"
SETTINGS_MODULE = Path("app/core/config/settings.py")
ENV_EXAMPLE_PATH = ROOT / ".env.example"
BUDGETS_PATH = ROOT / ".github" / "simplicity-budgets.toml"
ENV_PREFIX = "CODEX_LB_"
ENV_ONLY_TIERS = frozenset({"T0", "T1"})
DASHBOARD_SETTINGS_TABLE = "dashboard_settings"

# Process-level environment reads that are NOT Settings fields (third-party or POSIX
# conventions; documented in docs/reference/settings.md "Process-level environment
# variables"). Paths are repo-relative; the value is the number of lines in that file that
# reference the environment today (the per-file ratchet: more lines fail, fewer lines warn so
# the cap gets lowered, an entry whose file no longer reads the environment warns so it gets
# dropped) plus a note on what is read. Caps only ever shrink.
ENV_READ_ALLOWLIST: Mapping[str, tuple[int, str]] = {
    "app/main.py": (1, "PORT (host runs)"),
    "app/cli.py": (7, "HOST/PORT/SSL_*/UVICORN_* uvicorn launch knobs"),
    "app/core/metrics/prometheus.py": (1, "PROMETHEUS_MULTIPROC_DIR"),
    "app/core/utils/proxy_env.py": (4, "HTTP(S)_PROXY/ALL_PROXY/WS_PROXY/NO_PROXY outbound proxy"),
    "app/core/clients/http.py": (3, "outbound proxy env fallback"),
    "app/core/clients/proxy_websocket.py": (2, "outbound proxy env fallback"),
    "app/modules/runtime/service.py": (1, "GITHUB_TOKEN release-version lookup"),
    "app/modules/telemetry/snapshot.py": (1, "KUBERNETES_SERVICE_HOST deployment detection"),
    "app/modules/automations/service.py": (1, "TZ default schedule timezone (S13)"),
    "app/db/session.py": (2, "CODEX_LB_TEST_DATABASE_URL (CI only)"),
    "app/db/alembic/versions/20260312_000000_add_additional_usage_quota_key.py": (1, "migration-time registry path"),
    "app/codex_sessions_retag.py": (3, "CODEX_HOME/USERPROFILE/WSL_DISTRO_NAME (standalone CLI tool)"),
}

_ENV_VAR_RE = re.compile(rf"\b{ENV_PREFIX}([A-Z0-9_]+)\b")
_HOME_TARGET_RE = re.compile(r"^([a-z_][a-z0-9_]*)\.([a-z_][a-z0-9_]*)$")


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def error(self, message: str) -> None:
        self.errors.append(message)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def check_tier_coverage(fields: Iterable[str], tiers: Mapping[str, str], valid_tiers: Iterable[str]) -> Report:
    report = Report()
    field_set = set(fields)
    valid = set(valid_tiers)
    for name in sorted(field_set - set(tiers)):
        report.error(f"Settings.{name} has no tier in app/core/config/tiers.py SETTING_TIERS")
    for name in sorted(set(tiers) - field_set):
        report.warn(f"SETTING_TIERS lists {name!r}, which is no longer a Settings field; drop the entry")
    for name, tier in sorted(tiers.items()):
        if tier not in valid:
            report.error(f"SETTING_TIERS[{name!r}] = {tier!r} is not one of {sorted(valid)}")
    return report


def check_t3_dashboard_home(
    fields: Iterable[str],
    tiers: Mapping[str, str],
    migrating: Mapping[str, str],
    dashboard_columns: Iterable[str],
    dashboard_homes: Mapping[str, str] | None = None,
    table_columns: Mapping[str, Iterable[str]] | None = None,
) -> Report:
    """T3 fields need a database home: same-name column, ``DASHBOARD_HOMES`` target, or ``MIGRATING``.

    ``table_columns`` maps every database table to its column names so a
    ``DASHBOARD_HOMES`` target (``table.column``) can be verified; when omitted,
    only ``dashboard_settings`` (``dashboard_columns``) is known.
    """
    report = Report()
    columns = set(dashboard_columns)
    homes = dict(dashboard_homes or {})
    tables = {table: set(names) for table, names in (table_columns or {}).items()}
    tables.setdefault(DASHBOARD_SETTINGS_TABLE, columns)
    field_set = set(fields)
    for name, target in sorted(homes.items()):
        # Redundant entries (field gone, not T3, or a same-name column now exists)
        # are classified first and only warn: the entry is due for deletion, so its
        # target is moot even when the old column was dropped in the same change.
        # Only an entry that actually serves as a live T3 field's home must resolve.
        if name not in field_set:
            report.warn(f"DASHBOARD_HOMES lists {name!r}, which is no longer a Settings field; drop the entry")
            continue
        if tiers.get(name) != "T3":
            report.warn(f"DASHBOARD_HOMES lists {name!r}, which is {tiers.get(name)!r}, not T3; drop the entry")
            continue
        if name in columns:
            report.warn(f"DASHBOARD_HOMES lists {name!r}, but {DASHBOARD_SETTINGS_TABLE}.{name} exists; drop the entry")
            continue
        match = _HOME_TARGET_RE.match(target)
        if match is None:
            report.error(f"DASHBOARD_HOMES[{name!r}] = {target!r} is not a 'table.column' target")
        elif match.group(2) not in tables.get(match.group(1), set()):
            report.error(f"DASHBOARD_HOMES maps {name!r} to {target!r}, but no such database column exists")
    for name in sorted(field_set):
        if tiers.get(name) != "T3":
            continue
        if name in columns or name in homes or name in migrating:
            continue
        report.error(
            f"Settings.{name} is T3 but has neither a dashboard_settings column of the same name, a "
            "DASHBOARD_HOMES mapping, nor a MIGRATING entry in app/core/config/tiers.py"
        )
    for name in sorted(migrating):
        if name not in field_set:
            report.warn(f"MIGRATING lists {name!r}, which is no longer a Settings field; drop the entry")
        elif tiers.get(name) != "T3":
            report.warn(f"MIGRATING lists {name!r}, which is {tiers.get(name)!r}, not T3; drop the entry")
        elif name in columns:
            report.warn(f"MIGRATING lists {name!r}, but dashboard_settings.{name} exists; drop the entry")
        elif name in homes:
            report.warn(
                f"MIGRATING lists {name!r}, but DASHBOARD_HOMES already maps it to {homes[name]!r}; drop the entry"
            )
    return report


def _env_read_lines(source: str, filename: str) -> list[int]:
    """Line numbers of ``os.environ`` / ``os.getenv`` / ``dotenv_values`` references."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise ValueError(f"{filename}: cannot parse ({exc.msg} at line {exc.lineno})") from exc
    direct_names: set[str] = set()
    os_names: set[str] = {"os"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module in {"os", "dotenv"}:
            for alias in node.names:
                if alias.name in {"environ", "getenv", "dotenv_values"}:
                    direct_names.add(alias.asname or alias.name)
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "os" and alias.asname:
                    os_names.add(alias.asname)
    lines: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in {"environ", "getenv"}:
            if isinstance(node.value, ast.Name) and node.value.id in os_names:
                lines.add(node.lineno)
        elif isinstance(node, ast.Attribute) and node.attr == "dotenv_values":
            lines.add(node.lineno)
        elif isinstance(node, ast.Name) and node.id in direct_names:
            lines.add(node.lineno)
    return sorted(lines)


def check_env_reads(app_dir: Path, root: Path, allowlist: Mapping[str, tuple[int, str]]) -> Report:
    report = Report()
    seen_allowlisted: set[str] = set()
    for path in sorted(app_dir.rglob("*.py")):
        rel = path.relative_to(root).as_posix()
        if rel == SETTINGS_MODULE.as_posix():
            continue
        try:
            lines = _env_read_lines(path.read_text(encoding="utf-8"), rel)
        except ValueError as exc:
            report.error(str(exc))
            continue
        if not lines:
            continue
        if rel in allowlist:
            seen_allowlisted.add(rel)
            cap = allowlist[rel][0]
            if len(lines) > cap:
                report.error(
                    f"{rel}: {len(lines)} lines read the process environment (lines {', '.join(map(str, lines))}), "
                    f"over the ENV_READ_ALLOWLIST cap of {cap}; add a Settings field in {SETTINGS_MODULE} "
                    "instead of a new direct read (configuration-tiers)"
                )
            elif len(lines) < cap:
                report.warn(
                    f"ENV_READ_ALLOWLIST caps {rel!r} at {cap} but only {len(lines)} lines read the environment; "
                    f"lower the cap to {len(lines)}"
                )
            continue
        for lineno in lines:
            report.error(
                f"{rel}:{lineno}: reads the process environment directly; add a Settings field in "
                f"{SETTINGS_MODULE} instead (configuration-tiers)"
            )
    for rel in sorted(set(allowlist) - seen_allowlisted):
        report.warn(f"ENV_READ_ALLOWLIST entry {rel!r} no longer reads the environment; drop the entry")
    return report


def env_example_fields(text: str) -> list[str]:
    """Settings field names mentioned in ``.env.example`` (commented lines included)."""
    names: list[str] = []
    for match in _ENV_VAR_RE.finditer(text):
        name = match.group(1).lower()
        if name not in names:
            names.append(name)
    return names


def check_env_example(text: str, fields: Iterable[str], tiers: Mapping[str, str]) -> Report:
    report = Report()
    field_set = set(fields)
    for name in env_example_fields(text):
        if name not in field_set:
            continue
        tier = tiers.get(name)
        if tier is not None and tier not in ENV_ONLY_TIERS:
            report.error(
                f".env.example mentions {ENV_PREFIX}{name.upper()} ({tier}); only T0/T1 settings belong in the "
                "operator template"
            )
    return report


def settings_field_budget(budgets_text: str) -> int:
    config = tomllib.loads(budgets_text)
    try:
        return int(config["settings_fields"]["max"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"{BUDGETS_PATH.relative_to(ROOT)} is missing a valid [settings_fields].max: {exc!r}") from exc


def check_field_budget(field_count: int, budget: int) -> Report:
    report = Report()
    if field_count > budget:
        report.error(
            f"Settings has {field_count} fields, over the [settings_fields] budget of {budget} in "
            f"{BUDGETS_PATH.relative_to(ROOT)}; removing a field lowers the number, raising it needs a "
            "simplicity-budget discussion (PRINCIPLES.md P2, issue #1340)"
        )
    return report


def run_all() -> tuple[list[Report], str]:
    from app.core.config.settings import Settings
    from app.core.config.tiers import DASHBOARD_HOMES, MIGRATING, SETTING_TIERS, TIERS
    from app.db.models import Base, DashboardSettings

    fields = list(Settings.model_fields)
    columns = [column.name for column in DashboardSettings.__table__.columns]
    table_columns = {name: [column.name for column in table.columns] for name, table in Base.metadata.tables.items()}
    budget = settings_field_budget(BUDGETS_PATH.read_text(encoding="utf-8"))
    reports = [
        check_tier_coverage(fields, SETTING_TIERS, TIERS),
        check_t3_dashboard_home(fields, SETTING_TIERS, MIGRATING, columns, DASHBOARD_HOMES, table_columns),
        check_env_reads(APP_DIR, ROOT, ENV_READ_ALLOWLIST),
        check_env_example(ENV_EXAMPLE_PATH.read_text(encoding="utf-8"), fields, SETTING_TIERS),
        check_field_budget(len(fields), budget),
    ]
    summary = f"settings fields: {len(fields)}/{budget}; tiers: " + ", ".join(
        f"{tier}={sum(1 for name in fields if SETTING_TIERS.get(name) == tier)}" for tier in TIERS
    )
    return reports, summary


def main() -> int:
    try:
        reports, summary = run_all()
    except (OSError, ValueError) as exc:
        print(f"check_settings_tiers: config error: {exc}")
        return 2
    warnings = [message for report in reports for message in report.warnings]
    errors = [message for report in reports for message in report.errors]
    for message in warnings:
        print(f"WARN: {message}")
    for message in errors:
        print(f"ERROR: {message}")
    print(summary)
    if errors:
        print(f"check_settings_tiers: {len(errors)} violation(s)")
        return 1
    print("check_settings_tiers: ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
