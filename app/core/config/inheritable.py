"""Resolver for dashboard-tier settings that inherit an environment or code default.

One place combines the three layers (``configuration-tiers``): code default <
process environment < dashboard column. ``SettingsService`` uses it to build
the settings API's ``provenance`` map, and hot paths that read a dashboard
snapshot use it to derive the effective value without re-implementing the
precedence.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

type SettingSource = Literal["dashboard", "env", "default"]
type SettingScalar = int | float | str | bool


@dataclass(frozen=True, slots=True)
class InheritableValue[T: SettingScalar]:
    """Effective value of a dashboard-tier setting and where it came from.

    ``source`` is ``"dashboard"`` when the dashboard column is non-NULL,
    ``"env"`` when the column is NULL and the environment value differs from
    the code default, and ``"default"`` otherwise. ``env_value`` is ``None``
    for database-only settings that have no environment fallback.
    """

    value: T
    source: SettingSource
    env_value: T | None
    default: T


def resolve_inheritable[T: SettingScalar](
    column_value: T | None, env_value: T | None, default: T
) -> InheritableValue[T]:
    """Resolve one inheritable setting as code default < environment < dashboard.

    This is the only place that combines the three layers; call sites must not
    re-implement the precedence (``configuration-tiers``).
    """
    if column_value is not None:
        return InheritableValue(column_value, "dashboard", env_value, default)
    if env_value is not None and env_value != default:
        return InheritableValue(env_value, "env", env_value, default)
    return InheritableValue(default, "default", env_value, default)
