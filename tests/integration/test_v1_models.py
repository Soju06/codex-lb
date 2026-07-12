from __future__ import annotations

import json
from dataclasses import replace

import pytest

from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry
from app.core.types import JsonValue

pytestmark = pytest.mark.integration

BOOTSTRAP_MODEL_SLUGS = {
    "gpt-5.6-sol",
    "gpt-5.6-terra",
    "gpt-5.6-luna",
    "gpt-5.5",
    "gpt-5.4",
    "gpt-5.4-mini",
    "gpt-5.3-codex",
    "gpt-5.3-codex-spark",
    "gpt-5.2",
    "codex-auto-review",
}

EXPECTED_CORE_MODEL_PLANS = {
    "plus",
    "pro",
    "prolite",
    "team",
    "business",
    "enterprise",
    "edu",
    "education",
    "go",
    "hc",
    "finserv",
    "quorum",
    "self_serve_business_usage_based",
    "enterprise_cbp_usage_based",
}

EXPECTED_BOOTSTRAP_MINIMAL_CLIENT_VERSIONS = {
    "gpt-5.6-sol": "0.144.0",
    "gpt-5.6-terra": "0.144.0",
    "gpt-5.6-luna": "0.144.0",
    "gpt-5.5": "0.124.0",
    "gpt-5.4": "0.98.0",
    "gpt-5.4-mini": "0.98.0",
    "gpt-5.3-codex": "0.98.0",
    "gpt-5.3-codex-spark": "0.100.0",
    "gpt-5.2": "0.0.1",
    "codex-auto-review": "0.98.0",
}


def _make_upstream_model(
    slug: str,
    *,
    supported_in_api: bool = True,
    base_instructions: str = "",
    raw: dict[str, JsonValue] | None = None,
) -> UpstreamModel:
    default_raw: dict[str, JsonValue] = {
        "shell_type": "shell_command",
        "visibility": "list",
        "availability_nux": None,
    }
    return UpstreamModel(
        slug=slug,
        display_name=slug,
        description=f"Test model {slug}",
        context_window=272000,
        input_modalities=("text", "image"),
        supported_reasoning_levels=(ReasoningLevel(effort="medium", description="default"),),
        default_reasoning_level="medium",
        supports_reasoning_summaries=True,
        support_verbosity=False,
        default_verbosity=None,
        prefer_websockets=False,
        supports_parallel_tool_calls=True,
        supported_in_api=supported_in_api,
        minimal_client_version=None,
        priority=0,
        available_in_plans=frozenset({"plus", "pro"}),
        base_instructions=base_instructions,
        raw=raw or default_raw,
    )


async def _populate_test_registry() -> None:
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2"),
        _make_upstream_model("gpt-5.3-codex"),
    ]
    await registry.update({"plus": models, "pro": models})


async def _create_model_source(
    async_client,
    *,
    name: str,
    model: str,
    supports_responses: bool = False,
    supports_streaming: bool = True,
    context_window: int | None = 8192,
    raw_metadata_json: str | None = None,
) -> str:
    model_payload: dict[str, object] = {
        "model": model,
        "displayName": model,
        "maxOutputTokens": 1024,
        "supportsStreaming": supports_streaming,
        "supportsTools": True,
        "supportsVision": False,
    }
    if context_window is not None:
        model_payload["contextWindow"] = context_window
    if raw_metadata_json is not None:
        model_payload["rawMetadataJson"] = raw_metadata_json
    response = await async_client.post(
        "/api/model-sources/",
        json={
            "name": name,
            "baseUrl": f"https://{name}.example.invalid/v1",
            "apiKey": f"token-{name}",
            "supportsChatCompletions": True,
            "supportsResponses": supports_responses,
            "models": [model_payload],
        },
    )
    assert response.status_code == 200
    return response.json()["id"]


@pytest.mark.asyncio
async def test_v1_models_list(async_client):
    await _populate_test_registry()
    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["object"] == "list"
    data = payload["data"]
    assert isinstance(data, list)
    ids = {item["id"] for item in data}
    assert "gpt-5.2" in ids
    assert "gpt-5.3-codex" in ids
    custom_items = [item for item in data if item["id"] in {"gpt-5.2", "gpt-5.3-codex"}]
    for item in custom_items:
        assert item["object"] == "model"
        assert item["owned_by"] == "codex-lb"
        assert "metadata" in item
        assert item["api_types"] == ["chat_completions"]
        assert item["capabilities"]["context_length"] == item["metadata"]["input_context_window"]
        assert item["capabilities"]["supports_tool_use"] is True
        assert item["capabilities"]["supports_streaming"] is True
        assert item["capabilities"]["output_modalities"] == ["text"]
        assert item["contextLength"] == item["metadata"]["input_context_window"]
        assert item["context_length"] == item["metadata"]["input_context_window"]
        assert item["supportsReasoning"] is True
        assert item["supports_reasoning"] is True
        assert item["supportsImages"] is True
        assert item["supports_images"] is True
        assert item["supportsVision"] is True
        assert item["supports_vision"] is True


@pytest.mark.asyncio
async def test_v1_models_with_client_version_returns_codex_catalog(async_client):
    """Codex clients configured via `openai_base_url` fetch `<base>/models` with a
    `client_version` query parameter and can only parse the Codex catalog shape;
    the OpenAI-compatible list shape makes them silently fall back to bundled
    model metadata."""
    await _populate_test_registry()
    resp = await async_client.get("/v1/models", params={"client_version": "0.144.1"})
    assert resp.status_code == 200
    payload = resp.json()
    assert "models" in payload
    slugs = {entry["slug"] for entry in payload["models"]}
    assert "gpt-5.2" in slugs
    codex_resp = await async_client.get("/backend-api/codex/models")
    assert codex_resp.status_code == 200
    assert payload == codex_resp.json()


@pytest.mark.asyncio
async def test_v1_models_with_client_version_includes_model_messages(async_client):
    """The Codex model picker needs `model_messages` from the live catalog to
    display models absent from its bundled metadata; the catalog served on the
    `client_version` path must preserve the field verbatim."""
    registry = get_model_registry()
    model_messages: dict[str, JsonValue] = {
        "instructions_template": "You are a coding assistant.",
        "instructions_variables": {"personality_default": ""},
        "approvals": None,
    }
    models = [
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
                "model_messages": model_messages,
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/v1/models", params={"client_version": "0.144.1"})
    assert resp.status_code == 200
    entries = {entry["slug"]: entry for entry in resp.json()["models"]}
    assert entries["gpt-5.3-codex"]["model_messages"] == model_messages


@pytest.mark.asyncio
async def test_v1_models_with_empty_client_version_keeps_openai_shape(async_client):
    await _populate_test_registry()
    resp = await async_client.get("/v1/models?client_version=")
    assert resp.status_code == 200
    assert resp.json()["object"] == "list"


@pytest.mark.asyncio
async def test_v1_models_uses_bootstrap_models_when_registry_not_populated(async_client):
    registry = get_model_registry()
    registry._snapshot = None
    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    payload = resp.json()
    assert payload["object"] == "list"
    ids = {item["id"] for item in payload["data"]}
    assert ids == BOOTSTRAP_MODEL_SLUGS
    assert "gpt-5.5-pro" not in ids


@pytest.mark.asyncio
async def test_backend_codex_models_uses_bootstrap_upstream_metadata(async_client):
    registry = get_model_registry()
    registry._snapshot = None

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    entries = {entry["slug"]: entry for entry in resp.json()["models"]}

    assert set(entries) == set(EXPECTED_BOOTSTRAP_MINIMAL_CLIENT_VERSIONS)
    for slug, expected_version in EXPECTED_BOOTSTRAP_MINIMAL_CLIENT_VERSIONS.items():
        assert entries[slug]["minimal_client_version"] == expected_version

    sol = entries["gpt-5.6-sol"]
    assert sol["display_name"] == "GPT-5.6-Sol"
    assert sol["context_window"] == 372_000
    assert sol["default_reasoning_level"] == "low"
    assert {level["effort"] for level in sol["supported_reasoning_levels"]} == {
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    }
    assert sol["additional_speed_tiers"] == ["fast"]

    terra = entries["gpt-5.6-terra"]
    assert terra["default_reasoning_level"] == "medium"
    assert {level["effort"] for level in terra["supported_reasoning_levels"]} == {
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
        "ultra",
    }

    luna = entries["gpt-5.6-luna"]
    assert luna["default_reasoning_level"] == "medium"
    assert {level["effort"] for level in luna["supported_reasoning_levels"]} == {
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    }

    # Upstream-exact GPT-5.6 metadata as served on the Codex catalog wire
    # (codex-rs/models-manager/models.json at rust-v0.144.1).
    for gpt56 in (sol, terra, luna):
        assert gpt56["minimal_client_version"] == "0.144.0"
        assert gpt56["tool_mode"] == "code_mode_only"
        assert gpt56["use_responses_lite"] is True
        assert gpt56["apply_patch_tool_type"] == "freeform"
        assert gpt56["web_search_tool_type"] == "text_and_image"
        assert gpt56["truncation_policy"] == {"mode": "tokens", "limit": 10_000}
        assert gpt56["default_reasoning_summary"] == "none"
        assert gpt56["reasoning_summary_format"] == "experimental"
        assert gpt56["comp_hash"] == "3000"
        assert gpt56["experimental_supported_tools"] == []
        assert gpt56["max_context_window"] == 372_000
        assert gpt56["service_tiers"] == [
            {"id": "priority", "name": "Fast", "description": "1.5x speed, increased usage"}
        ]
        assert {"edu_plus", "edu_pro", "enterprise_cbp_automation", "sci"} <= set(gpt56["available_in_plans"])
    assert sol["multi_agent_version"] == "v2"
    assert terra["multi_agent_version"] == "v2"
    assert luna["multi_agent_version"] == "v1"
    assert "most capable model yet" in sol["availability_nux"]["message"]
    assert terra["availability_nux"] is None
    assert luna["availability_nux"] is None

    gpt54 = entries["gpt-5.4"]
    assert gpt54["minimal_client_version"] == "0.98.0"
    assert gpt54["max_context_window"] == 1_000_000
    assert set(gpt54["available_in_plans"]) == EXPECTED_CORE_MODEL_PLANS

    mini = entries["gpt-5.4-mini"]
    assert mini["prefer_websockets"] is True
    assert mini["default_verbosity"] == "medium"
    assert mini["minimal_client_version"] == "0.98.0"
    assert {level["effort"] for level in mini["supported_reasoning_levels"]} == {"low", "medium", "high", "xhigh"}

    spark = entries["gpt-5.3-codex-spark"]
    assert spark["context_window"] == 128_000
    assert spark["minimal_client_version"] == "0.100.0"
    assert spark["supported_in_api"] is True

    auto_review = entries["codex-auto-review"]
    assert auto_review["visibility"] == "hide"
    assert auto_review["shell_type"] == "shell_command"
    assert auto_review["max_context_window"] == 1_000_000
    assert auto_review["minimal_client_version"] == "0.98.0"
    assert set(auto_review["available_in_plans"]) == EXPECTED_CORE_MODEL_PLANS
    assert set(entries["gpt-5.3-codex"]["available_in_plans"]) == EXPECTED_CORE_MODEL_PLANS


@pytest.mark.asyncio
async def test_v1_models_excludes_supported_in_api_false_models(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2"),
        _make_upstream_model("gpt-5.3-codex"),
        _make_upstream_model("gpt-hidden", supported_in_api=False),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["data"]}
    assert "gpt-5.2" in ids
    assert "gpt-5.3-codex" in ids
    assert "gpt-hidden" not in ids


@pytest.mark.asyncio
async def test_v1_models_includes_supported_model_and_excludes_unsupported_spark_alias(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.3-codex", supported_in_api=False),
        _make_upstream_model("gpt-5.3-codex-spark", supported_in_api=True),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    ids = {item["id"] for item in resp.json()["data"]}
    assert "gpt-5.3-codex" not in ids
    assert "gpt-5.3-codex-spark" in ids


@pytest.mark.asyncio
async def test_v1_models_filters_openai_compatible_sources_by_api_key_assignment(async_client):
    first_source_id = await _create_model_source(async_client, name="vllm-first", model="vllm-visible")
    await _create_model_source(async_client, name="vllm-second", model="vllm-hidden")

    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert settings.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "source-scoped-key",
            "assignedSourceIds": [first_source_id],
        },
    )
    assert created.status_code == 200
    assert created.json()["sourceAssignmentScopeEnabled"] is True
    assert created.json()["assignedSourceIds"] == [first_source_id]

    response = await async_client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
    )
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["data"]}

    assert "vllm-visible" in ids
    assert "vllm-hidden" not in ids

    deleted = await async_client.delete(f"/api/model-sources/{first_source_id}")
    assert deleted.status_code == 204

    listed_keys = await async_client.get("/api/api-keys/")
    assert listed_keys.status_code == 200
    listed_key = next(row for row in listed_keys.json() if row["id"] == created.json()["id"])
    assert listed_key["sourceAssignmentScopeEnabled"] is True
    assert listed_key["assignedSourceIds"] == []

    after_delete = await async_client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
    )
    assert after_delete.status_code == 200
    ids_after_delete = {item["id"] for item in after_delete.json()["data"]}
    assert "vllm-hidden" not in ids_after_delete


@pytest.mark.asyncio
async def test_v1_models_filters_source_models_by_exact_allowlist(async_client):
    await _create_model_source(async_client, name="alias-source", model="gpt-5-high")
    await _create_model_source(async_client, name="plain-source", model="plain-source-model")

    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert settings.status_code == 200

    alias_key = await async_client.post(
        "/api/api-keys/",
        json={"name": "exact source alias key", "allowedModels": ["gpt-5-high"]},
    )
    assert alias_key.status_code == 200
    alias_response = await async_client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {alias_key.json()['key']}"},
    )
    assert alias_response.status_code == 200
    alias_ids = {item["id"] for item in alias_response.json()["data"]}
    assert "gpt-5-high" in alias_ids
    assert "plain-source-model" not in alias_ids

    canonical_key = await async_client.post(
        "/api/api-keys/",
        json={"name": "canonical source alias key", "allowedModels": ["gpt-5"]},
    )
    assert canonical_key.status_code == 200
    canonical_response = await async_client.get(
        "/v1/models",
        headers={"Authorization": f"Bearer {canonical_key.json()['key']}"},
    )
    assert canonical_response.status_code == 200
    canonical_ids = {item["id"] for item in canonical_response.json()["data"]}
    assert "gpt-5-high" not in canonical_ids


@pytest.mark.asyncio
async def test_backend_codex_models_filters_source_models_by_exact_allowlist(async_client):
    await _create_model_source(
        async_client,
        name="codex-alias-source",
        model="gpt-5-high",
        supports_responses=True,
    )
    await _create_model_source(
        async_client,
        name="codex-plain-source",
        model="plain-source-model",
        supports_responses=True,
    )

    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert settings.status_code == 200

    alias_key = await async_client.post(
        "/api/api-keys/",
        json={"name": "codex exact source alias key", "allowedModels": ["gpt-5-high"]},
    )
    assert alias_key.status_code == 200
    alias_response = await async_client.get(
        "/backend-api/codex/models",
        headers={"Authorization": f"Bearer {alias_key.json()['key']}"},
    )
    assert alias_response.status_code == 200
    alias_slugs = {item["slug"] for item in alias_response.json()["models"]}
    alias_data_ids = {item["id"] for item in alias_response.json()["data"]}
    assert "gpt-5-high" in alias_slugs
    assert "gpt-5-high" in alias_data_ids
    assert "plain-source-model" not in alias_slugs
    assert "plain-source-model" not in alias_data_ids

    canonical_key = await async_client.post(
        "/api/api-keys/",
        json={"name": "codex canonical source alias key", "allowedModels": ["gpt-5"]},
    )
    assert canonical_key.status_code == 200
    canonical_response = await async_client.get(
        "/backend-api/codex/models",
        headers={"Authorization": f"Bearer {canonical_key.json()['key']}"},
    )
    assert canonical_response.status_code == 200
    canonical_slugs = {item["slug"] for item in canonical_response.json()["models"]}
    canonical_data_ids = {item["id"] for item in canonical_response.json()["data"]}
    assert "gpt-5-high" not in canonical_slugs
    assert "gpt-5-high" not in canonical_data_ids


@pytest.mark.asyncio
async def test_backend_codex_models_includes_only_responses_capable_source_models(async_client):
    await _create_model_source(
        async_client,
        name="codex-source-responses",
        model="external-responses-model",
        supports_responses=True,
    )
    await _create_model_source(
        async_client,
        name="codex-source-chat",
        model="external-chat-only-model",
        supports_responses=False,
    )
    await _create_model_source(
        async_client,
        name="codex-source-non-streaming",
        model="external-non-streaming-responses-model",
        supports_responses=True,
        supports_streaming=False,
    )

    response = await async_client.get("/backend-api/codex/models")
    assert response.status_code == 200
    payload = response.json()
    slugs = {item["slug"] for item in payload["models"]}
    data_ids = {item["id"] for item in payload["data"]}

    assert "external-responses-model" in slugs
    assert "external-responses-model" in data_ids
    assert "external-chat-only-model" not in slugs
    assert "external-chat-only-model" not in data_ids
    assert "external-non-streaming-responses-model" not in slugs
    assert "external-non-streaming-responses-model" not in data_ids

    source_entry = next(item for item in payload["models"] if item["slug"] == "external-responses-model")
    assert "source_id" not in source_entry
    assert "source_kind" not in source_entry


@pytest.mark.asyncio
async def test_backend_codex_models_defaults_source_model_context_window(async_client):
    await _create_model_source(
        async_client,
        name="codex-source-default-context",
        model="external-default-context-model",
        supports_responses=True,
        context_window=None,
    )

    response = await async_client.get("/backend-api/codex/models")

    assert response.status_code == 200
    source_entry = next(item for item in response.json()["models"] if item["slug"] == "external-default-context-model")
    assert source_entry["context_window"] == 128_000
    assert source_entry["shell_type"] == "shell_command"
    assert source_entry["max_context_window"] == 128_000
    assert source_entry["truncation_policy"] == {"mode": "tokens", "limit": 10_000}
    assert source_entry["include_skills_usage_instructions"] is False
    assert source_entry["supports_image_detail_original"] is False
    assert source_entry["supports_search_tool"] is False
    assert source_entry["use_responses_lite"] is False
    assert source_entry["experimental_supported_tools"] == []
    assert source_entry["prefer_websockets"] is False


@pytest.mark.asyncio
async def test_backend_codex_models_never_exposes_source_request_overrides(async_client):
    await _create_model_source(
        async_client,
        name="codex-source-overrides",
        model="external-overrides-model",
        supports_responses=True,
        raw_metadata_json=json.dumps(
            {
                "source_request_overrides": {"options": {"num_ctx": 32768}, "temperature": 0.2},
                "supports_search_tool": True,
            }
        ),
    )

    response = await async_client.get("/backend-api/codex/models")

    assert response.status_code == 200
    payload = response.json()
    source_entry = next(item for item in payload["models"] if item["slug"] == "external-overrides-model")
    assert "source_request_overrides" not in source_entry
    # Capability metadata still flows through to the client-visible entry.
    assert source_entry["supports_search_tool"] is True
    # The operator override config must not leak anywhere in the catalog payload.
    assert "source_request_overrides" not in response.text


@pytest.mark.asyncio
async def test_backend_codex_models_returns_format1(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
                "availability_nux": None,
                "upgrade": {"model": "gpt-5.4", "migration_markdown": "Upgrade!"},
            },
        ),
        _make_upstream_model(
            "gpt-5.2",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    payload = resp.json()
    assert "models" in payload
    assert isinstance(payload["models"], list)
    slugs = {m["slug"] for m in payload["models"]}
    assert {"gpt-5.2", "gpt-5.3-codex"}.issubset(slugs)
    assert payload["object"] == "list"
    data_ids = {m["id"] for m in payload["data"]}
    assert {"gpt-5.2", "gpt-5.3-codex"}.issubset(data_ids)


@pytest.mark.asyncio
async def test_backend_codex_models_unions_service_tiers_across_accounts(async_client):
    # Issue #1100: one account/plan without Fast entitlement must not strip Fast
    # from the shared /backend-api/codex/models catalog.
    registry = get_model_registry()
    fast = _make_upstream_model(
        "gpt-5.5",
        raw={
            "shell_type": "shell_command",
            "visibility": "list",
            "service_tiers": [{"slug": "default"}, {"slug": "fast"}],
            "additional_speed_tiers": ["fast"],
        },
    )
    no_fast = _make_upstream_model(
        "gpt-5.5",
        raw={
            "shell_type": "shell_command",
            "visibility": "list",
            "service_tiers": [{"slug": "default"}],
            "additional_speed_tiers": [],
        },
    )
    # no-Fast plan iterated last; last-writer-wins would drop Fast from the catalog.
    await registry.update({"pro": [fast], "plus": [no_fast]})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    model = next(m for m in resp.json()["models"] if m["slug"] == "gpt-5.5")
    tier_slugs = {t.get("slug") for t in (model.get("service_tiers") or [])}
    assert "fast" in tier_slugs
    assert "fast" in (model.get("additional_speed_tiers") or [])


@pytest.mark.asyncio
async def test_backend_codex_models_does_not_reunion_stale_global_service_tiers(async_client):
    registry = get_model_registry()
    fast = _make_upstream_model(
        "gpt-5.5",
        raw={
            "shell_type": "shell_command",
            "visibility": "list",
            "service_tiers": [{"slug": "default"}, {"slug": "fast"}],
            "additional_speed_tiers": ["fast"],
        },
    )
    no_fast = _make_upstream_model(
        "gpt-5.5",
        raw={
            "shell_type": "shell_command",
            "visibility": "list",
            "service_tiers": [{"slug": "default"}],
            "additional_speed_tiers": [],
        },
    )

    await registry.update({"pro": [fast], "plus": [no_fast]})
    await registry.update({"plus": [no_fast]})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    model = next(m for m in resp.json()["models"] if m["slug"] == "gpt-5.5")
    tier_slugs = {t.get("slug") for t in (model.get("service_tiers") or [])}
    assert "fast" not in tier_slugs
    assert "fast" not in (model.get("additional_speed_tiers") or [])


@pytest.mark.asyncio
async def test_backend_codex_models_data_keeps_only_list_visible_models(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-visible",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
            },
        ),
        _make_upstream_model(
            "gpt-hidden",
            raw={
                "shell_type": "shell_command",
                "visibility": "hide",
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    payload = resp.json()
    assert {"gpt-visible", "gpt-hidden"}.issubset({m["slug"] for m in payload["models"]})
    data = {m["id"]: m for m in payload["data"]}
    assert "gpt-visible" in data
    assert "gpt-hidden" not in data
    assert data["gpt-visible"]["object"] == "model"
    assert data["gpt-visible"]["owned_by"] == "codex-lb"


@pytest.mark.asyncio
async def test_backend_codex_models_lists_codex_shell_models_not_supported_in_v1(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.3-codex-spark",
            supported_in_api=False,
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
            },
        ),
    ]
    await registry.update({"pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    payload = resp.json()
    entries = {m["slug"]: m for m in payload["models"]}

    assert entries["gpt-5.3-codex-spark"]["supported_in_api"] is False
    assert entries["gpt-5.3-codex-spark"]["visibility"] == "list"
    assert "gpt-5.3-codex-spark" not in {m["id"] for m in payload["data"]}


@pytest.mark.asyncio
async def test_backend_codex_models_excludes_unsupported_non_shell_models(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-internal",
            supported_in_api=False,
            raw={
                "shell_type": "internal",
                "visibility": "list",
            },
        ),
    ]
    await registry.update({"pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    payload = resp.json()
    assert "gpt-internal" not in {m["slug"] for m in payload["models"]}
    assert "gpt-internal" not in {m["id"] for m in payload["data"]}


@pytest.mark.asyncio
async def test_backend_codex_models_entry_has_upstream_fields(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
                "availability_nux": None,
                "upgrade": {"model": "gpt-5.4", "migration_markdown": "Upgrade!"},
                "model_messages": {
                    "instructions_template": "You are a coding assistant.",
                    "instructions_variables": {"personality_default": ""},
                    "approvals": None,
                },
            },
            base_instructions="You are a helpful coding assistant.",
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    entries = resp.json()["models"]
    entry = next(m for m in entries if m["slug"] == "gpt-5.3-codex")

    assert entry["display_name"] == "gpt-5.3-codex"
    assert entry["description"] == "Test model gpt-5.3-codex"
    assert entry["base_instructions"] == "You are a helpful coding assistant."
    assert entry["context_window"] == 272000
    assert entry["supported_in_api"] is True
    assert entry["shell_type"] == "shell_command"
    assert entry["visibility"] == "list"
    assert entry["availability_nux"] is None
    assert entry["upgrade"] == {"model": "gpt-5.4", "migration_markdown": "Upgrade!"}
    assert entry["model_messages"] == {
        "instructions_template": "You are a coding assistant.",
        "instructions_variables": {"personality_default": ""},
        "approvals": None,
    }


@pytest.mark.asyncio
async def test_backend_codex_models_preserves_upstream_visibility(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "hide",
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    entries = resp.json()["models"]
    entry = next(m for m in entries if m["slug"] == "gpt-5.3-codex")
    assert entry["visibility"] == "hide"


@pytest.mark.asyncio
async def test_backend_codex_models_filters_disallowed_models(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2", base_instructions="allowed"),
        _make_upstream_model("gpt-5.3-codex", base_instructions="blocked"),
    ]
    await registry.update({"plus": models, "pro": models})

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "codex-restricted",
            "allowedModels": ["gpt-5.2"],
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    resp = await async_client.get("/backend-api/codex/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200
    entries = resp.json()["models"]
    assert [entry["slug"] for entry in entries] == ["gpt-5.2"]
    assert entries[0]["base_instructions"] == "allowed"


@pytest.mark.asyncio
async def test_backend_codex_models_rewrites_visibility_when_opted_in(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.2",
            raw={
                "shell_type": "shell_command",
                "visibility": "hide",
            },
        ),
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
            },
        ),
        _make_upstream_model(
            "gpt-hidden",
            supported_in_api=False,
            raw={
                "shell_type": "internal",
                "visibility": "list",
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "codex-visibility",
            "allowedModels": ["gpt-5.2", "gpt-hidden"],
            "applyToCodexModel": True,
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    resp = await async_client.get("/backend-api/codex/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200

    entries = {entry["slug"]: entry for entry in resp.json()["models"]}
    assert {"gpt-5.2", "gpt-5.3-codex"}.issubset(entries)
    assert "gpt-hidden" not in entries
    assert entries["gpt-5.2"]["visibility"] == "list"
    assert entries["gpt-5.3-codex"]["visibility"] == "hide"


@pytest.mark.asyncio
async def test_backend_codex_models_visibility_allowlist_respects_enforced_model(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.2",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
            },
        ),
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "hide",
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "codex-visibility-enforced",
            "allowedModels": ["gpt-5.2", "gpt-5.3-codex"],
            "applyToCodexModel": True,
            "enforcedModel": "gpt-5.3-codex",
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    resp = await async_client.get("/backend-api/codex/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200

    entries = {entry["slug"]: entry for entry in resp.json()["models"]}
    assert {"gpt-5.2", "gpt-5.3-codex"}.issubset(entries)
    assert entries["gpt-5.2"]["visibility"] == "hide"
    assert entries["gpt-5.3-codex"]["visibility"] == "list"


@pytest.mark.asyncio
async def test_model_catalogs_canonicalize_enforced_model_alias(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.2",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
            },
        ),
        _make_upstream_model(
            "gpt-5.4-mini",
            raw={
                "shell_type": "shell_command",
                "visibility": "hide",
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "catalog-enforced-alias",
            "allowedModels": ["gpt-5.4-mini-high"],
            "applyToCodexModel": True,
            "enforcedModel": "gpt-5.4-mini-high",
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    v1_resp = await async_client.get("/v1/models", headers={"Authorization": f"Bearer {key}"})
    assert v1_resp.status_code == 200
    assert [entry["id"] for entry in v1_resp.json()["data"]] == ["gpt-5.4-mini"]

    codex_resp = await async_client.get("/backend-api/codex/models", headers={"Authorization": f"Bearer {key}"})
    assert codex_resp.status_code == 200
    entries = {entry["slug"]: entry for entry in codex_resp.json()["models"]}
    assert {"gpt-5.2", "gpt-5.4-mini"}.issubset(entries)
    assert entries["gpt-5.2"]["visibility"] == "hide"
    assert entries["gpt-5.4-mini"]["visibility"] == "list"


@pytest.mark.asyncio
async def test_backend_codex_models_preserves_original_flow_without_allowlist(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.2",
            raw={
                "shell_type": "shell_command",
                "visibility": "hide",
            },
        ),
        _make_upstream_model(
            "gpt-5.3-codex",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200

    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "codex-visibility-no-allowlist",
            "applyToCodexModel": True,
        },
    )
    assert created.status_code == 200
    key = created.json()["key"]

    resp = await async_client.get("/backend-api/codex/models", headers={"Authorization": f"Bearer {key}"})
    assert resp.status_code == 200

    entries = {entry["slug"]: entry for entry in resp.json()["models"]}
    assert {"gpt-5.2", "gpt-5.3-codex"}.issubset(entries)
    assert entries["gpt-5.2"]["visibility"] == "hide"
    assert entries["gpt-5.3-codex"]["visibility"] == "list"


@pytest.mark.asyncio
async def test_backend_codex_models_keeps_supported_in_api_false_entries_out_of_data(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2"),
        _make_upstream_model("gpt-5.3-codex"),
        _make_upstream_model(
            "gpt-5.3-codex-spark",
            supported_in_api=False,
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    slugs = {m["slug"] for m in resp.json()["models"]}
    assert "gpt-5.2" in slugs
    assert "gpt-5.3-codex" in slugs
    assert "gpt-5.3-codex-spark" in slugs
    assert "gpt-5.3-codex-spark" not in {m["id"] for m in resp.json()["data"]}


@pytest.mark.asyncio
async def test_backend_codex_models_uses_bootstrap_models_when_registry_not_populated(async_client):
    registry = get_model_registry()
    registry._snapshot = None
    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    payload = resp.json()
    slugs = {item["slug"] for item in payload["models"]}
    assert slugs == BOOTSTRAP_MODEL_SLUGS
    data_ids = {item["id"] for item in payload["data"]}
    assert data_ids.issubset(BOOTSTRAP_MODEL_SLUGS)
    assert data_ids
    assert "gpt-5.5-pro" not in slugs
    assert all(not slug.startswith("gpt-image-") for slug in slugs)


@pytest.mark.asyncio
async def test_model_sets_are_consistent_across_api_endpoints(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2"),
        _make_upstream_model("gpt-5.3-codex"),
        _make_upstream_model(
            "gpt-hidden",
            supported_in_api=False,
            raw={
                "shell_type": "internal",
                "visibility": "list",
            },
        ),
    ]
    await registry.update({"plus": models, "pro": models})

    dashboard = await async_client.get("/api/models")
    v1 = await async_client.get("/v1/models")
    codex = await async_client.get("/backend-api/codex/models")

    assert dashboard.status_code == 200
    assert v1.status_code == 200
    assert codex.status_code == 200

    dashboard_ids = {item["id"] for item in dashboard.json()["models"]}
    v1_ids = {item["id"] for item in v1.json()["data"]}
    codex_slugs = {item["slug"] for item in codex.json()["models"]}
    assert "gpt-hidden" not in dashboard_ids
    assert "gpt-hidden" not in v1_ids
    assert "gpt-hidden" not in codex_slugs
    assert dashboard_ids == v1_ids
    assert dashboard_ids.issubset(codex_slugs)


@pytest.mark.asyncio
async def test_dashboard_models_exposes_extended_reasoning_efforts(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.6-sol",
            raw={
                "shell_type": "shell_command",
                "visibility": "list",
            },
        )
    ]
    models[0] = replace(
        models[0],
        supported_reasoning_levels=tuple(
            ReasoningLevel(effort=effort, description=effort)
            for effort in ("low", "medium", "high", "xhigh", "max", "ultra")
        ),
        default_reasoning_level="low",
    )
    await registry.update({"plus": models, "pro": models})

    response = await async_client.get("/api/models")

    assert response.status_code == 200
    model = next(item for item in response.json()["models"] if item["id"] == "gpt-5.6-sol")
    assert model["supportedReasoningEfforts"] == ["low", "medium", "high", "xhigh", "max", "ultra"]
    assert model["defaultReasoningEffort"] == "low"


@pytest.mark.asyncio
async def test_model_context_window_override(async_client, monkeypatch):
    registry = get_model_registry()
    models = [_make_upstream_model("gpt-5.4")]
    await registry.update({"pro": models})

    from app.core.config.settings import get_settings
    from app.modules.proxy import api as proxy_api_module

    original_settings = get_settings()
    patched = original_settings.model_copy(update={"model_context_window_overrides": {"gpt-5.4": 515000}})
    monkeypatch.setattr(proxy_api_module, "get_settings", lambda: patched)

    # /backend-api/codex/models
    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    entry = next(m for m in resp.json()["models"] if m["slug"] == "gpt-5.4")
    assert entry["context_window"] == 515000

    # /v1/models
    resp_v1 = await async_client.get("/v1/models")
    assert resp_v1.status_code == 200
    v1_entry = next(m for m in resp_v1.json()["data"] if m["id"] == "gpt-5.4")
    metadata = v1_entry["metadata"]
    assert metadata["context_window"] == 515000
    assert metadata["input_context_window"] == 272000
    assert v1_entry["capabilities"]["context_length"] == 272000
    assert v1_entry["contextLength"] == 272000
    assert v1_entry["context_length"] == 272000


@pytest.mark.asyncio
async def test_model_context_window_no_override(async_client):
    registry = get_model_registry()
    models = [_make_upstream_model("gpt-5.4")]
    await registry.update({"pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    entry = next(m for m in resp.json()["models"] if m["slug"] == "gpt-5.4")
    assert entry["context_window"] == 272000


def _raw_with_max_context_window(max_context_window: int) -> dict[str, JsonValue]:
    return {
        "shell_type": "shell_command",
        "visibility": "list",
        "max_context_window": max_context_window,
        "auto_compact_token_limit": None,
    }


@pytest.mark.asyncio
async def test_v1_models_reports_backend_context_window(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.4", raw=_raw_with_max_context_window(1_000_000)),
        _make_upstream_model("gpt-5.5", raw=_raw_with_max_context_window(272_000)),
        _make_upstream_model("gpt-5.4-mini", raw=_raw_with_max_context_window(272_000)),
        _make_upstream_model("gpt-5.3-codex", raw=_raw_with_max_context_window(272_000)),
    ]
    await registry.update({"pro": models})

    resp_v1 = await async_client.get("/v1/models")
    assert resp_v1.status_code == 200
    metadata_by_id = {item["id"]: item["metadata"] for item in resp_v1.json()["data"]}

    for slug in ("gpt-5.4", "gpt-5.5", "gpt-5.4-mini", "gpt-5.3-codex"):
        metadata = metadata_by_id[slug]
        assert metadata["context_window"] == 272_000
        assert metadata["input_context_window"] == 272_000
        assert metadata["max_output_tokens"] == 128_000
        entry = next(item for item in resp_v1.json()["data"] if item["id"] == slug)
        assert entry["api_types"] == ["chat_completions"]
        assert entry["capabilities"]["context_length"] == 272_000
        assert entry["capabilities"]["max_output_tokens"] == 128_000
        assert entry["capabilities"]["supports_reasoning"] is True
        assert entry["capabilities"]["supportsImages"] is True
        assert entry["capabilities"]["supports_images"] is True
        assert entry["capabilities"]["supports_vision"] is True
        assert entry["capabilities"]["supports_tool_use"] is True
        assert entry["capabilities"]["supports_streaming"] is True
        assert entry["capabilities"]["output_modalities"] == ["text"]
        assert entry["contextLength"] == 272_000
        assert entry["context_length"] == 272_000
        assert entry["maxOutputTokens"] == 128_000
        assert entry["max_output_tokens"] == 128_000

    resp_codex = await async_client.get("/backend-api/codex/models")
    assert resp_codex.status_code == 200
    codex_by_slug = {item["slug"]: item for item in resp_codex.json()["models"]}
    assert codex_by_slug["gpt-5.4"]["context_window"] == 272_000
    assert codex_by_slug["gpt-5.4"]["max_context_window"] == 1_000_000
    assert codex_by_slug["gpt-5.5"]["context_window"] == 272_000
    assert codex_by_slug["gpt-5.5"]["max_context_window"] == 272_000


@pytest.mark.asyncio
async def test_v1_models_exposes_speed_tier_metadata(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model(
            "gpt-5.5",
            raw={
                "additional_speed_tiers": ["fast"],
                "default_service_tier": "priority",
                "service_tiers": [
                    {
                        "id": "priority",
                        "name": "Fast",
                        "description": "1.5x speed, increased usage",
                    }
                ],
            },
        )
    ]
    await registry.update({"pro": models})

    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    entry = next(item for item in resp.json()["data"] if item["id"] == "gpt-5.5")
    metadata = entry["metadata"]

    assert metadata["additional_speed_tiers"] == ["fast"]
    assert metadata["default_service_tier"] == "priority"
    assert metadata["service_tiers"] == [
        {
            "id": "priority",
            "name": "Fast",
            "description": "1.5x speed, increased usage",
        }
    ]


@pytest.mark.asyncio
async def test_v1_models_omits_speed_tier_metadata_when_upstream_omits_it(async_client):
    registry = get_model_registry()
    models = [_make_upstream_model("gpt-5.5")]
    await registry.update({"pro": models})

    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    entry = next(item for item in resp.json()["data"] if item["id"] == "gpt-5.5")
    metadata = entry["metadata"]

    assert "additional_speed_tiers" not in metadata
    assert "default_service_tier" not in metadata
    assert "service_tiers" not in metadata


@pytest.mark.asyncio
async def test_v1_models_does_not_promote_raw_max_context_window(async_client):
    registry = get_model_registry()
    models = [_make_upstream_model("gpt-custom", raw=_raw_with_max_context_window(900_000))]
    await registry.update({"pro": models})

    resp = await async_client.get("/v1/models")
    assert resp.status_code == 200
    entry = next(item for item in resp.json()["data"] if item["id"] == "gpt-custom")

    assert entry["metadata"]["context_window"] == 272_000
    assert entry["metadata"]["input_context_window"] == 272_000
    assert entry["metadata"].get("max_output_tokens") is None


@pytest.mark.asyncio
async def test_codex_catalog_hides_last_known_metadata_omitted_by_live_refresh(async_client):
    registry = get_model_registry()
    sol = _make_upstream_model(
        "gpt-5.6-sol",
        base_instructions="full live sol instructions",
        raw={
            "shell_type": "shell_command",
            "visibility": "list",
            "use_responses_lite": True,
        },
    )
    terra = _make_upstream_model("gpt-5.6-terra")
    await registry.update({"plus": [sol, terra]})
    await registry.update({"plus": [terra]})

    codex_resp = await async_client.get(
        "/backend-api/codex/models",
        params={"client_version": "0.144.1"},
    )
    assert codex_resp.status_code == 200
    entries = {item["slug"]: item for item in codex_resp.json()["models"]}
    assert entries["gpt-5.6-sol"]["visibility"] == "hide"
    assert entries["gpt-5.6-sol"]["base_instructions"] == "full live sol instructions"
    assert entries["gpt-5.6-sol"]["use_responses_lite"] is True
    assert entries["gpt-5.6-terra"]["visibility"] == "list"

    v1_resp = await async_client.get("/v1/models")
    assert v1_resp.status_code == 200
    assert "gpt-5.6-sol" not in {item["id"] for item in v1_resp.json()["data"]}


@pytest.mark.asyncio
async def test_source_model_shadows_retained_metadata_with_the_same_slug(async_client):
    registry = get_model_registry()
    sol = _make_upstream_model("gpt-5.6-sol", base_instructions="retained subscription metadata")
    terra = _make_upstream_model("gpt-5.6-terra")
    await registry.update({"plus": [sol, terra]})
    await registry.update({"plus": [terra]})
    await _create_model_source(
        async_client,
        name="live-sol-source",
        model="gpt-5.6-sol",
        supports_responses=True,
    )

    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert settings.status_code == 200
    created = await async_client.post(
        "/api/api-keys/",
        json={"name": "exact live sol source", "allowedModels": ["gpt-5.6-sol"]},
    )
    assert created.status_code == 200

    response = await async_client.get(
        "/backend-api/codex/models",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
    )
    assert response.status_code == 200
    entries = [item for item in response.json()["models"] if item["slug"] == "gpt-5.6-sol"]
    assert len(entries) == 1
    assert entries[0]["visibility"] == "list"
    assert "gpt-5.6-sol" in {item["id"] for item in response.json()["data"]}


@pytest.mark.asyncio
async def test_filtered_same_slug_source_does_not_hide_allowed_retained_metadata(async_client):
    registry = get_model_registry()
    sol = _make_upstream_model("gpt-5.6-sol", base_instructions="retained subscription metadata")
    terra = _make_upstream_model("gpt-5.6-terra")
    await registry.update({"plus": [sol, terra]})
    await registry.update({"plus": [terra]})
    await _create_model_source(
        async_client,
        name="filtered-live-sol-source",
        model="gpt-5.6-sol",
        supports_responses=True,
    )

    settings = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert settings.status_code == 200
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "canonical retained sol alias",
            "allowedModels": ["gpt-5.6-sol-extra-high-fast"],
        },
    )
    assert created.status_code == 200

    response = await async_client.get(
        "/backend-api/codex/models",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
    )
    assert response.status_code == 200
    entries = [item for item in response.json()["models"] if item["slug"] == "gpt-5.6-sol"]
    assert len(entries) == 1
    assert entries[0]["visibility"] == "hide"
    assert entries[0]["base_instructions"] == "retained subscription metadata"
    assert "gpt-5.6-sol" not in {item["id"] for item in response.json()["data"]}


@pytest.mark.asyncio
async def test_raw_hidden_same_slug_source_does_not_shadow_retained_metadata(async_client):
    registry = get_model_registry()
    sol = _make_upstream_model(
        "gpt-5.6-sol",
        base_instructions="retained sol metadata",
        raw={"use_responses_lite": True},
    )
    terra = _make_upstream_model("gpt-5.6-terra")
    await registry.update({"plus": [sol, terra]})
    await registry.update({"plus": [terra]})
    await _create_model_source(
        async_client,
        name="raw-hidden-same-slug-sol-source",
        model="gpt-5.6-sol",
        supports_responses=True,
        raw_metadata_json=json.dumps({"visibility": "hide", "use_responses_lite": False}),
    )
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "raw-hidden-source-codex-visibility",
            "allowedModels": ["gpt-5.6-sol"],
            "applyToCodexModel": True,
        },
    )
    assert created.status_code == 200

    response = await async_client.get(
        "/backend-api/codex/models",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
    )

    assert response.status_code == 200
    entries = {item["slug"]: item for item in response.json()["models"]}
    assert entries["gpt-5.6-sol"]["visibility"] == "hide"
    assert entries["gpt-5.6-sol"]["base_instructions"] == "retained sol metadata"
    assert entries["gpt-5.6-sol"]["use_responses_lite"] is True
    assert "gpt-5.6-sol" not in {item["id"] for item in response.json()["data"]}


@pytest.mark.asyncio
async def test_list_visible_same_slug_source_wins_over_earlier_hidden_source(async_client):
    registry = get_model_registry()
    sol = _make_upstream_model("gpt-5.6-sol", base_instructions="retained sol metadata")
    terra = _make_upstream_model("gpt-5.6-terra")
    await registry.update({"plus": [sol, terra]})
    await registry.update({"plus": [terra]})
    await _create_model_source(
        async_client,
        name="first-hidden-same-slug-source",
        model="gpt-5.6-sol",
        supports_responses=True,
        raw_metadata_json=json.dumps({"visibility": "hide", "use_responses_lite": False}),
    )
    await _create_model_source(
        async_client,
        name="second-visible-same-slug-source",
        model="gpt-5.6-sol",
        supports_responses=True,
        raw_metadata_json=json.dumps({"visibility": "list", "use_responses_lite": True}),
    )
    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "visible-source-precedence",
            "allowedModels": ["gpt-5.6-sol"],
            "applyToCodexModel": True,
        },
    )
    assert created.status_code == 200

    response = await async_client.get(
        "/backend-api/codex/models",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
    )

    assert response.status_code == 200
    entries = [item for item in response.json()["models"] if item["slug"] == "gpt-5.6-sol"]
    assert len(entries) == 1
    assert entries[0]["visibility"] == "list"
    assert entries[0]["use_responses_lite"] is True
    assert "gpt-5.6-sol" in {item["id"] for item in response.json()["data"]}


@pytest.mark.asyncio
async def test_first_partial_refresh_serves_hidden_bootstrap_metadata(async_client):
    registry = get_model_registry()
    await registry.update({"plus": [_make_upstream_model("gpt-5.6-terra")]})

    codex_resp = await async_client.get("/backend-api/codex/models")
    entries = {item["slug"]: item for item in codex_resp.json()["models"]}
    assert entries["gpt-5.6-sol"]["visibility"] == "hide"
    assert entries["gpt-5.6-sol"]["use_responses_lite"] is True

    v1_resp = await async_client.get("/v1/models")
    assert "gpt-5.6-sol" not in {item["id"] for item in v1_resp.json()["data"]}


@pytest.mark.asyncio
async def test_codex_visibility_allowlist_keeps_retained_metadata_hidden(async_client):
    registry = get_model_registry()
    sol = _make_upstream_model("gpt-5.6-sol", base_instructions="retained sol")
    terra = _make_upstream_model("gpt-5.6-terra")
    await registry.update({"plus": [sol, terra]})
    await registry.update({"plus": [terra]})

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "retained-codex-visibility",
            "allowedModels": ["gpt-5.6-terra"],
            "applyToCodexModel": True,
        },
    )
    assert created.status_code == 200

    resp = await async_client.get(
        "/backend-api/codex/models",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
    )
    entries = {item["slug"]: item for item in resp.json()["models"]}
    assert entries["gpt-5.6-terra"]["visibility"] == "list"
    assert entries["gpt-5.6-sol"]["visibility"] == "hide"
    assert entries["gpt-5.6-sol"]["base_instructions"] == "retained sol"


@pytest.mark.asyncio
async def test_hidden_same_slug_source_does_not_shadow_retained_metadata_for_codex_visibility_allowlist(
    async_client,
):
    registry = get_model_registry()
    sol = _make_upstream_model(
        "gpt-5.6-sol",
        base_instructions="retained sol metadata",
        raw={"use_responses_lite": True},
    )
    terra = _make_upstream_model("gpt-5.6-terra")
    await registry.update({"plus": [sol, terra]})
    await registry.update({"plus": [terra]})
    await _create_model_source(
        async_client,
        name="hidden-same-slug-sol-source",
        model="gpt-5.6-sol",
        supports_responses=True,
    )

    enable = await async_client.put(
        "/api/settings",
        json={
            "stickyThreadsEnabled": False,
            "preferEarlierResetAccounts": False,
            "totpRequiredOnLogin": False,
            "apiKeyAuthEnabled": True,
        },
    )
    assert enable.status_code == 200
    created = await async_client.post(
        "/api/api-keys/",
        json={
            "name": "retained-codex-visibility-alias",
            "allowedModels": ["gpt-5.6-sol-extra-high-fast"],
            "applyToCodexModel": True,
        },
    )
    assert created.status_code == 200

    resp = await async_client.get(
        "/backend-api/codex/models",
        headers={"Authorization": f"Bearer {created.json()['key']}"},
    )

    assert resp.status_code == 200
    entries = {item["slug"]: item for item in resp.json()["models"]}
    assert entries["gpt-5.6-sol"]["visibility"] == "hide"
    assert entries["gpt-5.6-sol"]["base_instructions"] == "retained sol metadata"
    assert entries["gpt-5.6-sol"]["use_responses_lite"] is True
    assert "gpt-5.6-sol" not in {item["id"] for item in resp.json()["data"]}
