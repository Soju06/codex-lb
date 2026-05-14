from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_backend_api_codex_v1_models_aliases_canonical(async_client) -> None:
    """/backend-api/codex/v1/models must return the same payload as
    /backend-api/codex/models -- the alias middleware should strip the
    duplicated /v1/ segment before routing so the canonical handler
    runs unchanged.
    """
    canonical = await async_client.get("/backend-api/codex/models")
    aliased = await async_client.get("/backend-api/codex/v1/models")

    assert canonical.status_code == aliased.status_code == 200
    assert canonical.json() == aliased.json()


@pytest.mark.asyncio
async def test_top_level_v1_models_is_unaffected(async_client) -> None:
    """/v1/models is the canonical OpenAI-style namespace; the alias
    middleware must not interfere with it.
    """
    response = await async_client.get("/v1/models")
    assert response.status_code == 200
