from __future__ import annotations

import pytest

from app.core.openai.model_registry import ReasoningLevel, UpstreamModel, get_model_registry
from app.core.types import JsonValue

pytestmark = pytest.mark.integration

BOOTSTRAP_MODEL_SLUGS = {
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
    for item in data:
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
    assert {m["slug"] for m in payload["models"]} == {"gpt-visible", "gpt-hidden"}
    assert {m["id"] for m in payload["data"]} == {"gpt-visible"}
    assert payload["data"][0]["object"] == "model"
    assert payload["data"][0]["owned_by"] == "codex-lb"


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
    assert set(entries) == {"gpt-5.2", "gpt-5.3-codex"}
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
    assert set(entries) == {"gpt-5.2", "gpt-5.3-codex"}
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
    assert set(entries) == {"gpt-5.2", "gpt-5.4-mini"}
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
    assert set(entries) == {"gpt-5.2", "gpt-5.3-codex"}
    assert entries["gpt-5.2"]["visibility"] == "hide"
    assert entries["gpt-5.3-codex"]["visibility"] == "list"


@pytest.mark.asyncio
async def test_backend_codex_models_excludes_supported_in_api_false_models(async_client):
    registry = get_model_registry()
    models = [
        _make_upstream_model("gpt-5.2"),
        _make_upstream_model("gpt-5.3-codex"),
        _make_upstream_model("gpt-hidden", supported_in_api=False),
    ]
    await registry.update({"plus": models, "pro": models})

    resp = await async_client.get("/backend-api/codex/models")
    assert resp.status_code == 200
    slugs = {m["slug"] for m in resp.json()["models"]}
    assert "gpt-5.2" in slugs
    assert "gpt-5.3-codex" in slugs
    assert "gpt-hidden" not in slugs


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
        _make_upstream_model("gpt-hidden", supported_in_api=False),
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
    assert dashboard_ids == v1_ids == codex_slugs


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
