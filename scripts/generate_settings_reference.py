#!/usr/bin/env python3
"""Render ``docs/reference/settings.md`` from ``Settings.model_fields``.

Usage::

    uv run python scripts/generate_settings_reference.py

The generated page is checked in so the docs build stays hermetic;
``tests/unit/test_settings_reference.py`` regenerates it and fails when the
page drifts from ``app/core/config/settings.py``.
"""

from __future__ import annotations

import enum
import types
import typing
from pathlib import Path
from typing import cast

from pydantic import AliasChoices
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from app.core.config.settings import _REMOVED_SETTINGS, Settings
from app.core.config.tiers import DASHBOARD_HOMES, SETTING_TIERS
from app.db.models import DashboardSettings

REPO_ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = REPO_ROOT / "docs" / "reference" / "settings.md"

ENV_PREFIX = "CODEX_LB_"

# Defaults computed from the runtime environment (home directory, container
# detection, hostname, outbound proxy env vars). Rendered symbolically so the
# generated page is machine-independent and deterministic.
_SYMBOLIC_DEFAULTS: dict[str, str] = {
    "data_dir": "`~/.codex-lb` (host) / `/var/lib/codex-lb` (container)",
    "database_url": "`sqlite+aiosqlite:///<data_dir>/store.db`",
    "encryption_key_file": "`<data_dir>/encryption.key`",
    "conversation_archive_dir": "`<data_dir>/conversation-archive`",
    "oauth_callback_host": "`127.0.0.1` (host) / `0.0.0.0` (container)",
    "upstream_websocket_trust_env": "auto-detected from outbound proxy env vars",
    "http_responses_session_bridge_instance_id": "process hostname",
}

_SECTION_OTHER = "Other"

# Functional-area grouping by field-name prefix; the longest matching prefix
# wins and unmatched fields land in the "Other" bucket.
_PREFIX_SECTIONS: tuple[tuple[str, str], ...] = (
    ("database_", "Database"),
    ("encryption_", "Encryption"),
    ("upstream_", "Upstream transport"),
    ("http_responses_session_bridge_", "HTTP Responses session bridge"),
    ("http_responses_", "HTTP & streaming"),
    ("http_connector_", "HTTP & streaming"),
    ("compact_", "HTTP & streaming"),
    ("stream_", "HTTP & streaming"),
    ("sse_", "HTTP & streaming"),
    ("max_", "HTTP & streaming"),
    ("transcription_", "HTTP & streaming"),
    ("proxy_", "Proxy admission & account caps"),
    ("oauth_", "OAuth"),
    ("token_refresh_", "Token refresh"),
    ("auth_guardian_", "Token refresh"),
    ("usage_", "Usage"),
    ("live_usage_", "Usage"),
    ("rate_limit_", "Usage"),
    ("openai_", "Prompt caching & affinity"),
    ("image_", "Images"),
    ("images_", "Images"),
    ("model_", "Model registry"),
    ("firewall_", "Firewall"),
    ("dashboard_", "Dashboard"),
    ("conversation_archive_", "Conversation archive"),
    ("quota_planner_", "Schedulers"),
    ("automations_", "Schedulers"),
    ("sticky_session_", "Schedulers"),
    ("leader_election_", "Multi-replica"),
    ("metrics_", "Observability"),
    ("otel_", "Observability"),
    ("log_", "Observability"),
    ("circuit_breaker_", "Resilience & load shedding"),
    ("soft_drain_", "Resilience & load shedding"),
    ("deterministic_failover_", "Resilience & load shedding"),
    ("backpressure_", "Resilience & load shedding"),
    ("bulkhead_", "Resilience & load shedding"),
    ("memory_", "Resilience & load shedding"),
    ("shutdown_", "Resilience & load shedding"),
)

# Exact-name overrides applied before prefix matching.
_EXACT_SECTIONS: dict[str, str] = {
    "data_dir": "Core",
    "trace": "Observability",
    "connect_address": "Dashboard",
    "additional_quota_registry_file": "Usage",
    "forwarded_allow_ips": "Firewall",
}

# Process-level environment variables codex-lb honors that are NOT Settings
# fields: third-party or POSIX conventions read by the launcher, libraries, or
# frozen Alembic migrations. Listed so operators can find them and so the
# ``os.environ``-outside-Settings lint allowlist has a documented source.
_PROCESS_ENV_CONVENTIONS: tuple[tuple[str, str], ...] = (
    (
        "`HOST`, `PORT`, `SSL_CERTFILE`, `SSL_KEYFILE`, `UVICORN_TIMEOUT_KEEP_ALIVE`, `UVICORN_WS_MAX_SIZE`",
        "Uvicorn launch defaults read once by the `codex-lb` CLI (`app/cli.py`); host runs only.",
    ),
    (
        "`HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, `WS_PROXY`, `NO_PROXY` (and lowercase)",
        "Outbound proxy conventions honored by httpx/aiohttp/websockets for upstream egress.",
    ),
    (
        "`REQUEST_METHOD`",
        "CGI marker; when present `HTTP_PROXY` is ignored (httpoxy guard, mirrors the httpx/requests rule).",
    ),
    (
        "`TZ`",
        "POSIX process timezone; automation schedules with the `server_default` timezone resolve "
        "to it (falling back to the host local zone, then UTC).",
    ),
    (
        "`PROMETHEUS_MULTIPROC_DIR`",
        "prometheus_client multiprocess-mode convention.",
    ),
    (
        "`GITHUB_TOKEN`",
        "Optional bearer token for the GitHub latest-release version check.",
    ),
    (
        "`POD_IP`, `POD_NAME`, `HOSTNAME`, `KUBERNETES_SERVICE_HOST`",
        "Kubernetes/pod identity used for multi-replica validation and deployment-kind telemetry.",
    ),
    (
        "`CODEX_HOME`, `USERPROFILE`, `WSL_DISTRO_NAME`",
        "Codex CLI home discovery for the `codex-lb codex-sessions retag` tool.",
    ),
    (
        "`CODEX_LB_TEST_DATABASE_URL`",
        "Test-suite/CI only: overrides the database used by the test session factory.",
    ),
    (
        "`CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE` (inside `app/db/alembic/versions/**` only)",
        "Frozen Alembic migrations read the process environment directly because migrations must not "
        "depend on `Settings`; the live setting of the same name is documented in the tables below.",
    ),
)

_SECTION_ORDER: tuple[str, ...] = (
    "Core",
    "Database",
    "Encryption",
    "Upstream transport",
    "HTTP & streaming",
    "HTTP Responses session bridge",
    "Proxy admission & account caps",
    "OAuth",
    "Token refresh",
    "Usage",
    "Prompt caching & affinity",
    "Images",
    "Model registry",
    "Firewall",
    "Dashboard",
    "Conversation archive",
    "Schedulers",
    "Multi-replica",
    "Observability",
    "Resilience & load shedding",
    _SECTION_OTHER,
)


def _section_for(name: str) -> str:
    exact = _EXACT_SECTIONS.get(name)
    if exact is not None:
        return exact
    matches = [(len(prefix), section) for prefix, section in _PREFIX_SECTIONS if name.startswith(prefix)]
    if not matches:
        return _SECTION_OTHER
    return max(matches)[1]


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|")


def _render_type(annotation: object) -> str:
    if annotation is type(None):
        return "None"
    origin = typing.get_origin(annotation)
    if origin is typing.Literal:
        return " | ".join(repr(arg) for arg in typing.get_args(annotation))
    if origin is types.UnionType:
        return " | ".join(_render_type(arg) for arg in typing.get_args(annotation))
    if isinstance(annotation, type):
        if issubclass(annotation, enum.Enum):
            return " | ".join(repr(member.value) for member in annotation)
        return annotation.__name__
    return str(annotation)


def _env_names(name: str, field: FieldInfo) -> list[str]:
    alias = field.validation_alias
    if isinstance(alias, AliasChoices):
        return [choice for choice in alias.choices if isinstance(choice, str)]
    if isinstance(alias, str):
        return [alias]
    return [f"{ENV_PREFIX}{name.upper()}"]


def _render_env_cell(name: str, field: FieldInfo) -> str:
    primary, *aliases = _env_names(name, field)
    cell = f"`{primary}`"
    if aliases:
        cell += " (alias " + ", ".join(f"`{alias}`" for alias in aliases) + ")"
    return cell


def _render_default(name: str, field: FieldInfo) -> str:
    symbolic = _SYMBOLIC_DEFAULTS.get(name)
    if symbolic is not None:
        return symbolic
    default: object = field.default
    if default is PydanticUndefined:
        factory = field.default_factory
        if factory is None:
            return "required"
        default = cast("typing.Callable[[], object]", factory)()
    if isinstance(default, enum.Enum):
        default = default.value
    return f"`{default!r}`"


_DASHBOARD_COLUMNS = frozenset(column.name for column in DashboardSettings.__table__.columns)


def _render_tier_cell(name: str) -> str:
    tier = SETTING_TIERS.get(name, "unassigned")
    # A T3 setting with a same-name dashboard_settings column (or a DASHBOARD_HOMES
    # table) is managed from the dashboard; the env var is only the fallback while
    # the dashboard holds no value.
    if tier == "T3" and (name in _DASHBOARD_COLUMNS or name in DASHBOARD_HOMES):
        return "T3 (dashboard)"
    return tier


def _render_section_table(names: list[str], fields: dict[str, FieldInfo]) -> list[str]:
    with_description = any(fields[name].description for name in names)
    lines: list[str] = []
    if with_description:
        lines.append("| Environment variable | Tier | Type | Default | Description |")
        lines.append("| --- | --- | --- | --- | --- |")
    else:
        lines.append("| Environment variable | Tier | Type | Default |")
        lines.append("| --- | --- | --- | --- |")
    for name in names:
        field = fields[name]
        env_var = _render_env_cell(name, field)
        tier_cell = _render_tier_cell(name)
        type_cell = _escape_cell(f"`{_render_type(field.annotation)}`")
        default_cell = _escape_cell(_render_default(name, field))
        row = f"| {env_var} | {tier_cell} | {type_cell} | {default_cell} |"
        if with_description:
            row += f" {_escape_cell(field.description or '')} |"
        lines.append(row)
    return lines


def render_settings_reference() -> str:
    fields = dict(Settings.model_fields)
    sections: dict[str, list[str]] = {}
    for name in sorted(fields):
        sections.setdefault(_section_for(name), []).append(name)
    unknown = set(sections) - set(_SECTION_ORDER)
    if unknown:
        raise RuntimeError(f"sections missing from _SECTION_ORDER: {sorted(unknown)}")

    lines: list[str] = [
        "<!-- GENERATED — edit scripts/generate_settings_reference.py, not this file. -->",
        "",
        "# Settings Reference",
        "",
        "**GENERATED** — edit `scripts/generate_settings_reference.py`, not this file.",
        "Regenerate with `uv run python scripts/generate_settings_reference.py`;",
        "`tests/unit/test_settings_reference.py` fails when this page drifts from",
        "`app/core/config/settings.py`.",
        "",
        f"codex-lb currently exposes {len(fields)} settings. Every setting is an environment",
        f"variable, normally with the `{ENV_PREFIX}` prefix (process environment or `.env` /",
        "`.env.local` next to the process); aliased settings list every accepted name.",
        "All defaults work with zero configuration —",
        "start from [Configuration](../configuration.md) for the handful that matter,",
        "and treat everything else as advanced operational tunables.",
        "",
        "## Tiers",
        "",
        "The **Tier** column is the configuration policy for each setting",
        "(`app/core/config/tiers.py`, enforced by `scripts/check_settings_tiers.py`):",
        "",
        "- **T0** bootstrap — needed before the database is reachable; env only.",
        "- **T1** instance topology — legitimately differs per replica or deployment; env only.",
        "- **T2** secret — encrypted in the database; env is at most a seed.",
        "- **T3** behaviour tunable / feature flag — the dashboard is the management",
        "  surface. `T3 → dashboard` marks a setting that already has a",
        "  `dashboard_settings` column of the same name: the dashboard value wins and",
        "  the variable is a deprecated fallback. `T3 (env, migrating)` marks the",
        "  remaining env-only backlog.",
        "- **T4** incident debug — env allowed, dashboard toggle recommended.",
        "",
        "## `PORT` (special case, no prefix)",
        "",
        "The listen port (default `2455`) is read from the bare `PORT` process",
        f"environment variable, not a `{ENV_PREFIX}*` setting, and applies to host",
        "(uvx/local) runs only — env files map only prefixed variables. In Docker the",
        "container always listens on 2455 (the entrypoint pins `--port 2455`); change",
        "the host side of the compose `ports` mapping instead.",
        "",
        f"## `{ENV_PREFIX}ENV_FILE` (special case, bootstrap only)",
        "",
        "`.env` / `.env.local` are discovered next to the installed module root (the",
        f"repository checkout). `{ENV_PREFIX}ENV_FILE` — an `os.pathsep`-separated list",
        "of paths — overrides that discovery for installs whose module root cannot",
        "contain env files (the Nix package wrapper points it at the launch",
        "directory). It must be set in the process environment, not in an env file:",
        "the env-file locations have to be known before env files are read.",
        "",
        f"## `{ENV_PREFIX}WORKERS_PER_INSTANCE` (special case, startup guard)",
        "",
        "Not a setting: the only supported value is `1` (one worker process per",
        "instance), so there is nothing to configure. If it is set to anything else,",
        "startup fails with a settings validation error — per-account concurrency caps",
        "are partitioned per replica via the bridge ring, and multiple worker processes",
        "inside one instance would silently multiply them. Scale horizontally via",
        "replicas instead.",
        "",
        "## Process-level environment variables (not settings)",
        "",
        "These are third-party or POSIX conventions codex-lb honors without",
        "making them settings. They are read by their owning launcher, library, or",
        "frozen migration rather than through `Settings`. Together with `Settings`",
        "itself this table is the allowlist of environment reads under `app/`;",
        "anything else belongs in `app/core/config/settings.py`.",
        "",
        "| Environment variable(s) | Consumer |",
        "| --- | --- |",
    ]
    lines.extend(f"| {names} | {_escape_cell(purpose)} |" for names, purpose in _PROCESS_ENV_CONVENTIONS)

    for section in _SECTION_ORDER:
        names = sections.get(section)
        if not names:
            continue
        lines.append("")
        lines.append(f"## {section}")
        lines.append("")
        lines.extend(_render_section_table(names, fields))

    lines.extend(
        [
            "",
            "## Removed",
            "",
            "Images and default account probes choose `gpt-5.6-luna`, then `gpt-5.5`,",
            "using registry plan visibility and suppression. If neither qualifies, they",
            "use `gpt-5.6-luna`. Catalog visibility does not guarantee account access.",
            "There is no host-model setting. See the",
            "[Images spec](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/images-api-compat)",
            "and [probe spec](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/usage-refresh-policy).",
            "",
            "Removed settings (ignored with a one-release startup warning; each is now a",
            "fixed default or a dashboard runtime setting — see PRINCIPLES.md P2 /",
            "issue [#1340](https://github.com/Soju06/codex-lb/issues/1340)):",
            "",
        ]
    )
    lines.extend(f"- `{name}`" for name in _REMOVED_SETTINGS)
    lines.extend(
        [
            "",
            "---",
            "",
            "*Specs: [user-documentation]"
            "(https://github.com/Soju06/codex-lb/tree/main/openspec/specs/user-documentation) · "
            "[responses-api-compat]"
            "(https://github.com/Soju06/codex-lb/tree/main/openspec/specs/responses-api-compat) · "
            "[rate-limit-reset-credits]"
            "(https://github.com/Soju06/codex-lb/tree/main/openspec/specs/rate-limit-reset-credits) · "
            "[deployment-installation]"
            "(https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-installation) · "
            "[proxy-runtime-observability]"
            "(https://github.com/Soju06/codex-lb/tree/main/openspec/specs/proxy-runtime-observability)*",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> None:
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(render_settings_reference(), encoding="utf-8")
    print(f"wrote {OUTPUT_PATH.relative_to(REPO_ROOT)}")


if __name__ == "__main__":
    main()
