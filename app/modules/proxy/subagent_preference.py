from __future__ import annotations

from collections.abc import Awaitable, Callable, Collection
from typing import Any, cast

from app.core.config.settings_cache import get_settings_cache
from app.db.models import StickySessionKind
from app.modules.proxy._service.support import _call_with_supported_optional_kwargs
from app.modules.proxy.affinity import _AffinityPolicy
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.repo_bundle import ProxyRepoFactory

_PREFERENCE_MODES = frozenset({"parent_bound_only", "always"})


async def select_account_with_subagent_preference(
    select: Callable[..., Awaitable[Any]],
    deadline: float,
    repo_factory: ProxyRepoFactory,
    affinity_policy: _AffinityPolicy | None,
    kwargs: dict[str, object],
) -> AccountSelection:
    """Run one budgeted account selection, steering a fresh subagent off its parent's account.

    The parent is excluded for a single preferred attempt only. When that attempt
    finds no account, selection reruns with the caller's original exclusions so
    the preference never fails a request the parent account could serve.
    """
    required_capability_kwargs = {}
    if kwargs.get("require_security_work_authorized") is True:
        required_capability_kwargs["require_security_work_authorized"] = kwargs.pop("require_security_work_authorized")
    original_excluded = set(cast(Collection[str], kwargs.get("exclude_account_ids") or ()))
    mode = await _enabled_preference_mode(affinity_policy)
    parent_owner_id = None
    if (
        mode is not None
        and affinity_policy is not None
        and kwargs.get("request_stage", "first_turn") == "first_turn"
        and kwargs.get("preferred_account_id") is None
    ):
        parent_owner_id = await _parent_owner_to_avoid(repo_factory, affinity_policy, mode)
    if parent_owner_id is not None and parent_owner_id not in original_excluded:
        kwargs["exclude_account_ids"] = {*original_excluded, parent_owner_id}
    else:
        parent_owner_id = None

    selection = cast(
        AccountSelection,
        await _call_with_supported_optional_kwargs(
            select, deadline, optional_kwargs=kwargs, **required_capability_kwargs
        ),
    )
    if selection.account is None and parent_owner_id is not None:
        kwargs["exclude_account_ids"] = original_excluded
        selection = cast(
            AccountSelection,
            await _call_with_supported_optional_kwargs(
                select, deadline, optional_kwargs=kwargs, **required_capability_kwargs
            ),
        )
    # With the preference off, no request pays for a marker write.
    marker_key = affinity_policy.response_bound_thread_marker_key if affinity_policy is not None else None
    if (
        mode is not None
        and selection.account is not None
        and marker_key is not None
        and kwargs.get("preferred_account_is_continuity_owner") is True
    ):
        async with repo_factory() as repos:
            await repos.sticky_sessions.upsert(marker_key, selection.account.id, kind=StickySessionKind.CODEX_SESSION)
    return selection


async def _enabled_preference_mode(policy: _AffinityPolicy | None) -> str | None:
    if policy is None or (
        policy.subagent_parent_selection_key is None and policy.response_bound_thread_marker_key is None
    ):
        return None
    mode = (await get_settings_cache().get()).subagent_account_preference
    return mode if mode in _PREFERENCE_MODES else None


async def _parent_owner_to_avoid(repo_factory: ProxyRepoFactory, policy: _AffinityPolicy, mode: str) -> str | None:
    parent_selection_key = policy.subagent_parent_selection_key
    parent_marker_key = policy.subagent_parent_response_marker_key
    if parent_selection_key is None or parent_marker_key is None or policy.selection_key is None or policy.kind is None:
        return None
    async with repo_factory() as repos:
        sticky_sessions = repos.sticky_sessions
        child_owner_id = await sticky_sessions.get_account_id(
            policy.selection_key,
            kind=policy.kind,
            max_age_seconds=policy.max_age_seconds,
        )
        # An established child mapping always wins over the placement preference.
        if child_owner_id is not None:
            return None
        # The response-bound marker records the exact route the parent's
        # continuations take, so it outranks a possibly stale soft mapping.
        response_bound_owner_id = await sticky_sessions.get_account_id(
            parent_marker_key,
            kind=StickySessionKind.CODEX_SESSION,
            max_age_seconds=policy.max_age_seconds,
        )
        if response_bound_owner_id is not None or mode != "always":
            return response_bound_owner_id
        return await sticky_sessions.get_account_id(
            parent_selection_key,
            kind=StickySessionKind.PROMPT_CACHE,
            max_age_seconds=policy.max_age_seconds,
        )
