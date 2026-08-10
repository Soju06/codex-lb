"""Shared model-source selection helpers.

Both the HTTP request handlers and the WebSocket session path need to decide
whether a requested model is served by an OpenAI-compatible model source.
Keeping that decision in one module stops the two transports from drifting
apart: previously only the HTTP handlers consulted model sources, so a
source-owned model requested over WebSocket fell through to subscription
account selection and was rejected upstream.
"""

from __future__ import annotations

from app.core.openai.model_registry import get_model_registry
from app.db.models import ModelSource
from app.db.session import detach_session_objects, get_background_session
from app.modules.api_keys.service import ApiKeyData
from app.modules.model_sources.repository import ModelSourcesRepository


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
) -> tuple[ModelSource, str] | None:
    """Resolve ``model`` to a Responses-capable model source, if any."""
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


async def responses_model_is_source_owned(model: str | None, api_key: ApiKeyData | None) -> bool:
    """True when ``model`` is served by an enabled Responses-capable source.

    Used by the WebSocket path, which cannot forward to a model source and must
    fail the session so the client falls back to the HTTP transport.

    The API key's ``enforced_model`` is considered alongside the requested
    model, matching how the HTTP handlers build their candidate list: an
    enforced model that resolves to a source must not slip through to
    subscription-account selection.
    """
    enforced = effective_model_for_api_key(api_key, model)
    if not model and not enforced:
        return False
    return (
        await select_responses_model_source(
            model or enforced or "",
            api_key,
            raw_model=enforced,
            require_streaming=True,
        )
        is not None
    )
