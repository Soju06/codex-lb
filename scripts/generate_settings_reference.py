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

from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

from app.core.config.settings import _REMOVED_SETTINGS, Settings

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
    ("http_downstream_", "HTTP & streaming"),
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
    ("usage_", "Usage & retention"),
    ("live_usage_", "Usage & retention"),
    ("rate_limit_", "Usage & retention"),
    ("request_log_", "Usage & retention"),
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
    "workers_per_instance": "Multi-replica",
}

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
    "Usage & retention",
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


def _render_section_table(names: list[str], fields: dict[str, FieldInfo]) -> list[str]:
    with_description = any(fields[name].description for name in names)
    lines: list[str] = []
    if with_description:
        lines.append("| Environment variable | Type | Default | Description |")
        lines.append("| --- | --- | --- | --- |")
    else:
        lines.append("| Environment variable | Type | Default |")
        lines.append("| --- | --- | --- |")
    for name in names:
        field = fields[name]
        env_var = f"`{ENV_PREFIX}{name.upper()}`"
        type_cell = _escape_cell(f"`{_render_type(field.annotation)}`")
        default_cell = _escape_cell(_render_default(name, field))
        row = f"| {env_var} | {type_cell} | {default_cell} |"
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

    deprecated_env_aliases = [
        f"{ENV_PREFIX}{name.upper()}" for name in sorted(fields) if name.endswith("_retention_days")
    ]

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
        f"variable with the `{ENV_PREFIX}` prefix (process environment or `.env` /",
        "`.env.local` next to the process). All defaults work with zero configuration —",
        "start from [Configuration](../configuration.md) for the handful that matter,",
        "and treat everything else as advanced operational tunables.",
        "",
        "## `PORT` (special case, no prefix)",
        "",
        "The listen port (default `2455`) is read from the bare `PORT` process",
        f"environment variable, not a `{ENV_PREFIX}*` setting, and applies to host",
        "(uvx/local) runs only — env files map only prefixed variables. In Docker the",
        "container always listens on 2455 (the entrypoint pins `--port 2455`); change",
        "the host side of the compose `ports` mapping instead.",
    ]

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
            "## Removed / deprecated",
            "",
            "Deprecated env aliases (still functional for one release; the dashboard",
            "runtime value wins when set):",
            "",
        ]
    )
    lines.extend(f"- `{alias}`" for alias in deprecated_env_aliases)
    lines.extend(
        [
            "",
            "Removed settings (ignored; values are now fixed — see PRINCIPLES.md P2 /",
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
            "[deployment-installation]"
            "(https://github.com/Soju06/codex-lb/tree/main/openspec/specs/deployment-installation)*",
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
