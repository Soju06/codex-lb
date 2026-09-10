"""Per-model context window overrides: dashboard rows over the environment dict.

``CODEX_LB_MODEL_CONTEXT_WINDOW_OVERRIDES`` (``slug -> window``) is a T3
setting whose dashboard home is the ``model_context_window_overrides`` table
(one row per slug) rather than a ``dashboard_settings`` column, because the
value is a mapping, not a scalar. The precedence of ``configuration-tiers``
still applies per slug: a dashboard row wins, a slug without a row inherits
the environment entry, and a slug with neither has no override (the catalog
reports the upstream window). There is no code default for a slug, so this
module carries its own per-slug resolver instead of ``resolve_inheritable``.

The catalog endpoints (``GET /v1/models``, ``GET /backend-api/codex/models``)
read the dashboard rows through ``ModelContextWindowOverridesCache`` (TTL +
the cross-replica ``settings`` invalidation namespace, like
``SettingsCache``) — never per model entry and never inside a lock.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Literal

import anyio

from app.core.cache.invalidation import NAMESPACE_SETTINGS, get_cache_invalidation_poller

type ContextWindowOverrideSource = Literal["dashboard", "env"]


@dataclass(frozen=True, slots=True)
class ContextWindowOverride:
    """Effective override for one slug and where it came from.

    ``env_value`` is the environment entry for the slug (``None`` when the
    environment has none), reported even while a dashboard row wins so the
    dashboard can show what clearing the row returns to.
    """

    slug: str
    context_window: int
    source: ContextWindowOverrideSource
    env_value: int | None


def resolve_context_window_overrides(
    dashboard: Mapping[str, int], environment: Mapping[str, int]
) -> dict[str, ContextWindowOverride]:
    """Combine dashboard rows and the environment dict, per slug, dashboard first.

    This is the only place that combines the two layers; the catalog and the
    settings API both derive their view from it.
    """
    resolved: dict[str, ContextWindowOverride] = {}
    for slug in sorted(set(dashboard) | set(environment)):
        env_value = environment.get(slug)
        if slug in dashboard:
            resolved[slug] = ContextWindowOverride(slug, dashboard[slug], "dashboard", env_value)
        else:
            assert env_value is not None
            resolved[slug] = ContextWindowOverride(slug, env_value, "env", env_value)
    return resolved


def effective_context_window_overrides(dashboard: Mapping[str, int], environment: Mapping[str, int]) -> dict[str, int]:
    """``slug -> effective window`` (dashboard row, else environment entry)."""
    return {
        slug: override.context_window
        for slug, override in resolve_context_window_overrides(dashboard, environment).items()
    }


class ModelContextWindowOverridesCache:
    """Process-wide snapshot of the dashboard override rows (``slug -> window``).

    Mirrors ``SettingsCache``: a short TTL bounds staleness for out-of-band
    database edits, and the settings API invalidates durably through the
    ``settings`` namespace so every replica drops its snapshot at once.
    """

    # An expired snapshot is never fresh, whatever ``time.monotonic()`` returns
    # this early after boot; 0.0 would read as fresh for the first TTL seconds
    # of process uptime.
    _EXPIRED = float("-inf")

    def __init__(self, *, ttl_seconds: float = 5.0) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._ttl_seconds = ttl_seconds
        self._cached: dict[str, int] | None = None
        self._cached_at = self._EXPIRED
        self._lock = anyio.Lock()

    async def get(self) -> Mapping[str, int]:
        now = time.monotonic()
        if self._cached is not None and now - self._cached_at < self._ttl_seconds:
            return self._cached

        async with self._lock:
            now = time.monotonic()
            if self._cached is not None and now - self._cached_at < self._ttl_seconds:
                return self._cached
            # Imported lazily: ``app.db.session`` builds the engine from the
            # process settings on import, which unit tests of the resolver
            # must not trigger.
            from app.db.session import SessionLocal
            from app.modules.settings.repository import ModelContextWindowOverridesRepository

            try:
                async with SessionLocal() as session:
                    rows = await ModelContextWindowOverridesRepository(session).by_slug()
            except Exception:
                # The catalog must not 500 because the database is briefly
                # unreachable: keep serving the last known rows (like
                # ``SettingsCache.cached_row``) and retry on the next call. The
                # timestamp is deliberately not refreshed.
                if self._cached is None:
                    raise
                return self._cached
            self._cached = rows
            self._cached_at = now
            return rows

    def clear(self) -> None:
        """Forget the snapshot entirely, without taking the lock.

        Only for process-local resets with no concurrent load (test teardown);
        every runtime path goes through ``invalidate``, which keeps the last
        known rows as a fallback.
        """
        self._cached = None
        self._cached_at = self._EXPIRED

    async def invalidate(self, *, propagate: bool = True) -> None:
        """Drop the snapshot and, unless ``propagate`` is False, durably bump the
        cross-replica ``settings`` namespace before returning.

        Only the snapshot's *freshness* is dropped, not the rows: the next
        ``get`` always reloads, but a load that fails while the database is
        briefly unreachable can still fall back to the last known rows instead
        of failing the catalog request. Settings-namespace bumps are frequent
        and mostly unrelated to this table, so forgetting the rows on every one
        of them would make the fallback almost never available.

        The expiry happens under the lock, like ``SettingsCache``: a catalog
        build already inside ``get`` and awaiting the database must finish
        before the snapshot is expired, otherwise it would install its pre-write
        rows afterwards and keep serving the old window until the TTL expires.
        The poller callback registered for the namespace passes
        ``propagate=False`` so a remote bump never re-bumps (feedback-loop
        prevention).
        """
        async with self._lock:
            self._cached_at = self._EXPIRED
        if propagate:
            poller = get_cache_invalidation_poller()
            if poller is not None:
                await poller.bump(NAMESPACE_SETTINGS)


_cache = ModelContextWindowOverridesCache()


def get_model_context_window_overrides_cache() -> ModelContextWindowOverridesCache:
    return _cache
