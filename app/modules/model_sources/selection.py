"""Shared model-source selection helpers.

Both the HTTP request handlers and the WebSocket session path need to decide
whether a requested model is served by an OpenAI-compatible model source.
Keeping that decision in one module stops the two transports from drifting
apart: previously only the HTTP handlers consulted model sources, so a
source-owned model requested over WebSocket fell through to subscription
account selection and was rejected upstream.
"""

from __future__ import annotations

import logging

from app.core.openai.model_registry import get_model_registry
from app.db.models import ModelSource
from app.db.session import detach_session_objects, get_background_session
from app.modules.api_keys.service import ApiKeyData
from app.modules.model_sources.repository import ModelSourcesRepository

logger = logging.getLogger(__name__)


def allowed_source_ids_for_api_key(api_key: ApiKeyData | None) -> set[str] | None:
    """Source ids an API key may use, or ``None`` when scoping is disabled."""
    if api_key is None or not api_key.source_assignment_scope_enabled:
        return None
    return set(api_key.assigned_source_ids)


async def select_responses_model_source(
    model: str,
    api_key: ApiKeyData | None,
    *,
    raw_model: str | None = None,
    require_streaming: bool = False,
    only_disabled: bool = False,
) -> tuple[ModelSource, str] | None:
    """Resolve ``model`` to a Responses-capable model source, if any.

    ``only_disabled`` inverts the enabled-state filter and leaves every other
    rule -- candidate order, API key model allowlist, source assignment scope,
    subscription-registry precedence, route shape, streaming -- untouched, so
    the answer is exactly "the source this request would have used, had the
    operator not switched it off". Callers use it after the ordinary lookup
    misses, to refuse the request instead of handing a source-owned model to a
    subscription account that cannot serve it. Sharing one function keeps the
    two lookups from drifting apart the way the transports once did.
    """
    assigned_source_ids = allowed_source_ids_for_api_key(api_key)
    exact_allowed_models = set(api_key.allowed_models) if api_key and api_key.allowed_models else None
    candidates = [candidate for candidate in (raw_model, model) if candidate]
    if not candidates:
        return None
    deduped_candidates = list(dict.fromkeys(candidates))
    registry_models = get_model_registry().get_models_with_fallback()
    async with get_background_session() as session:
        repository = ModelSourcesRepository(session)
        for candidate in deduped_candidates:
            if exact_allowed_models is not None and candidate not in exact_allowed_models:
                continue
            subscription_model = registry_models.get(candidate)
            if assigned_source_ids is None and subscription_model is not None:
                continue
            source = await repository.find_responses_source_for_model(
                candidate,
                allowed_source_ids=assigned_source_ids,
                require_streaming=require_streaming,
                only_disabled=only_disabled,
            )
            if source is not None:
                break
        else:
            source = None
        # ``close_session`` rolls back the read transaction, which would
        # expire the loaded row; detach it so the forwarding path can read
        # its attributes after this session boundary.
        detach_session_objects(session)
        return (source, candidate) if source is not None else None


def effective_model_for_api_key(api_key: ApiKeyData | None, requested_model: str | None) -> str | None:
    """The model an API key forces, falling back to the requested one."""
    if api_key is None or api_key.enforced_model is None:
        return requested_model
    return api_key.enforced_model


async def responses_model_is_source_owned(
    model: str | None,
    api_key: ApiKeyData | None,
    *,
    raw_model: str | None = None,
) -> bool:
    """True when ``model`` is served by a Responses-capable source, even a disabled one.

    Used by the WebSocket path, which cannot forward to a model source and must
    fail the session so the client falls back to the HTTP transport.

    Disabled-source ownership counts as ownership here: the WebSocket transport
    cannot serve a source-owned model regardless of the source's enabled state,
    and dispatching the turn to a subscription account instead produces the
    opaque upstream 400 (``The '<model>' model is not supported when using
    Codex with a ChatGPT account.``). Bouncing the turn to HTTP lands it on the
    disabled-source denial there, which answers 503 ``model_source_disabled``
    and names the actual condition. The second, ``only_disabled`` probe runs
    only after the ordinary lookup misses, so enabled sources keep resolving in
    a single query.

    The API key's ``enforced_model`` is considered alongside the requested
    model, matching how the HTTP handlers build their candidate list: an
    enforced model that resolves to a source must not slip through to
    subscription-account selection.

    ``raw_model`` is the client's requested model captured before request
    preparation normalized aliases (``gpt-5-high`` -> ``gpt-5``), mirroring the
    HTTP path's ``raw_source_model``: the caller has already substituted the
    API key's ``enforced_model`` and applied the fast-mode correction, so it is
    used verbatim as the leading source candidate. When omitted (request states
    that predate preparation, e.g. replayed turns), the raw candidate is
    derived from ``enforced_model``/``model`` as before.

    Resolution failures fail open to ``False``. This helper only gates the
    WebSocket transport, where the alternative is worse: the lookup runs after
    the turn's usage reservation is acquired but before it is registered for
    cleanup, so a propagating database error tears the whole session down and
    strands the reservation until the stale reaper runs. Failing open degrades
    to the pre-guard behaviour (the subscription upstream rejects the model),
    and source forwarding could not have worked anyway — it needs the same
    database for the source's credentials. The HTTP handlers deliberately do
    not use this helper: they call ``select_responses_model_source`` directly
    and must keep surfacing resolution errors rather than silently routing
    source traffic to a subscription account.
    """
    raw = raw_model if raw_model is not None else effective_model_for_api_key(api_key, model)
    if not model and not raw:
        return False
    try:
        if (
            await select_responses_model_source(
                model or raw or "",
                api_key,
                raw_model=raw,
                require_streaming=True,
            )
            is not None
        ):
            return True
        return (
            await select_responses_model_source(
                model or raw or "",
                api_key,
                raw_model=raw,
                require_streaming=True,
                only_disabled=True,
            )
            is not None
        )
    except Exception:
        logger.warning(
            "model_source_resolution_failed_open model=%s raw_model=%s",
            model,
            raw,
            exc_info=True,
        )
        return False
