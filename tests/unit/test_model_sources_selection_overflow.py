"""``select_overflow_model_source``: designated-source resolution (#2123 WP-C1, design v3 §4.5).

The resolver keeps ``select_responses_model_source``'s candidate order and API-key
``allowed_models`` filter but drops the subscription-registry precedence skip --
a registry slug such as ``gpt-5.5`` is exactly what overflows -- and looks the
model up on the designated source only (``allowed_source_ids={source_id}``).
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy import inspect as sa_inspect

from app.core.openai.model_registry import MODEL_SOURCE_KIND_OPENAI_COMPATIBLE, get_model_registry
from app.db.models import ModelSource, ModelSourceModel
from app.db.session import get_background_session
from app.modules.api_keys.service import ApiKeyData
from app.modules.model_sources.selection import select_overflow_model_source, select_responses_model_source

pytestmark = pytest.mark.unit

OVERFLOW_SOURCE_ID = "src_overflow"
OTHER_SOURCE_ID = "src_other"
CHAT_ONLY_SOURCE_ID = "src_chat_only"
REGISTRY_SLUG = "gpt-5.5"


def _api_key(
    *,
    allowed_models: list[str] | None = None,
    assigned_source_ids: list[str] | None = None,
) -> ApiKeyData:
    return ApiKeyData(
        id="key_overflow",
        name="overflow",
        key_prefix="sk-test-overflow",
        allowed_models=allowed_models,
        enforced_model=None,
        enforced_reasoning_effort=None,
        enforced_service_tier=None,
        expires_at=None,
        is_active=True,
        created_at=datetime(2026, 1, 1),
        last_used_at=None,
        source_assignment_scope_enabled=assigned_source_ids is not None,
        assigned_source_ids=list(assigned_source_ids or []),
    )


def _source(source_id: str, name: str, *, supports_responses: bool, models: list[ModelSourceModel]) -> ModelSource:
    return ModelSource(
        id=source_id,
        name=name,
        kind=MODEL_SOURCE_KIND_OPENAI_COMPATIBLE,
        base_url=f"http://127.0.0.1:1/{source_id}/v1",
        api_key_encrypted=None,
        is_enabled=True,
        supports_chat_completions=True,
        supports_responses=supports_responses,
        models=models,
    )


@pytest.fixture
async def seeded_sources(db_setup: bool) -> None:
    del db_setup
    async with get_background_session() as session:
        session.add_all(
            [
                _source(
                    OVERFLOW_SOURCE_ID,
                    "Overflow",
                    supports_responses=True,
                    models=[
                        ModelSourceModel(model=REGISTRY_SLUG, is_enabled=True, supports_streaming=True),
                        ModelSourceModel(model="local-coder", is_enabled=True, supports_streaming=False),
                        ModelSourceModel(model="switched-off", is_enabled=False, supports_streaming=True),
                    ],
                ),
                _source(
                    OTHER_SOURCE_ID,
                    "Other",
                    supports_responses=True,
                    models=[
                        ModelSourceModel(model=REGISTRY_SLUG, is_enabled=True, supports_streaming=True),
                        ModelSourceModel(model="other-only", is_enabled=True, supports_streaming=True),
                    ],
                ),
                _source(
                    CHAT_ONLY_SOURCE_ID,
                    "Chat only",
                    supports_responses=False,
                    models=[ModelSourceModel(model=REGISTRY_SLUG, is_enabled=True, supports_streaming=True)],
                ),
            ]
        )
        await session.commit()


async def test_registry_slug_resolves_on_the_designated_source(seeded_sources: None) -> None:
    assert REGISTRY_SLUG in get_model_registry().get_models_with_fallback()

    resolved = await select_overflow_model_source(OVERFLOW_SOURCE_ID, REGISTRY_SLUG, None)

    assert resolved is not None
    source, model = resolved
    assert (source.id, model) == (OVERFLOW_SOURCE_ID, REGISTRY_SLUG)
    # The ordinary resolver defers registry slugs to subscription accounts for an
    # unscoped caller; overflow deliberately does not (mutant: slug precedence kept).
    assert await select_responses_model_source(REGISTRY_SLUG, None) is None


async def test_only_the_designated_source_is_consulted(seeded_sources: None) -> None:
    other = await select_overflow_model_source(OTHER_SOURCE_ID, "other-only", None)
    assert other is not None and other[0].id == OTHER_SOURCE_ID

    # ``other-only`` is served by another source; the designated one does not list it.
    assert await select_overflow_model_source(OVERFLOW_SOURCE_ID, "other-only", None) is None
    # A Responses-incapable or unknown designation never resolves (mutant: slug selection).
    assert await select_overflow_model_source(CHAT_ONLY_SOURCE_ID, REGISTRY_SLUG, None) is None
    assert await select_overflow_model_source("src_missing", REGISTRY_SLUG, None) is None
    assert await select_overflow_model_source("", REGISTRY_SLUG, None) is None


async def test_candidate_order_prefers_the_raw_model(seeded_sources: None) -> None:
    resolved = await select_overflow_model_source(OVERFLOW_SOURCE_ID, "local-coder", None, raw_model=REGISTRY_SLUG)
    assert resolved is not None and resolved[1] == REGISTRY_SLUG

    fallback = await select_overflow_model_source(OVERFLOW_SOURCE_ID, REGISTRY_SLUG, None, raw_model="not-served")
    assert fallback is not None and fallback[1] == REGISTRY_SLUG

    assert await select_overflow_model_source(OVERFLOW_SOURCE_ID, "", None) is None
    assert await select_overflow_model_source(OVERFLOW_SOURCE_ID, "", None, raw_model=None) is None


async def test_api_key_allowed_models_filter_applies(seeded_sources: None) -> None:
    restricted = _api_key(allowed_models=[REGISTRY_SLUG])

    assert await select_overflow_model_source(OVERFLOW_SOURCE_ID, "local-coder", restricted) is None
    allowed = await select_overflow_model_source(OVERFLOW_SOURCE_ID, "local-coder", restricted, raw_model=REGISTRY_SLUG)
    assert allowed is not None and allowed[1] == REGISTRY_SLUG

    unrestricted = _api_key(allowed_models=[])
    assert await select_overflow_model_source(OVERFLOW_SOURCE_ID, "local-coder", unrestricted) is not None


async def test_source_assignment_scope_is_left_to_the_caller(seeded_sources: None) -> None:
    scoped_elsewhere = _api_key(assigned_source_ids=[OTHER_SOURCE_ID])

    resolved = await select_overflow_model_source(OVERFLOW_SOURCE_ID, REGISTRY_SLUG, scoped_elsewhere)

    assert resolved is not None and resolved[0].id == OVERFLOW_SOURCE_ID


async def test_require_streaming_filters_non_streaming_models(seeded_sources: None) -> None:
    assert await select_overflow_model_source(OVERFLOW_SOURCE_ID, "local-coder", None, require_streaming=True) is None

    resolved = await select_overflow_model_source(OVERFLOW_SOURCE_ID, "local-coder", None, require_streaming=False)
    assert resolved is not None and resolved[1] == "local-coder"

    streaming = await select_overflow_model_source(OVERFLOW_SOURCE_ID, REGISTRY_SLUG, None, require_streaming=True)
    assert streaming is not None


async def test_disabled_model_or_source_never_resolves(seeded_sources: None) -> None:
    assert await select_overflow_model_source(OVERFLOW_SOURCE_ID, "switched-off", None) is None

    async with get_background_session() as session:
        source = await session.get(ModelSource, OVERFLOW_SOURCE_ID)
        assert source is not None
        source.is_enabled = False
        await session.commit()

    assert await select_overflow_model_source(OVERFLOW_SOURCE_ID, REGISTRY_SLUG, None) is None


async def test_rows_are_detached_and_readable_after_the_session_closes(seeded_sources: None) -> None:
    resolved = await select_overflow_model_source(OVERFLOW_SOURCE_ID, REGISTRY_SLUG, None)

    assert resolved is not None
    source, _ = resolved
    assert sa_inspect(source).detached
    assert source.name == "Overflow"
    assert source.base_url.endswith(f"/{OVERFLOW_SOURCE_ID}/v1")
    assert {entry.model for entry in source.models} == {REGISTRY_SLUG, "local-coder", "switched-off"}
    assert all(sa_inspect(entry).detached for entry in source.models)
