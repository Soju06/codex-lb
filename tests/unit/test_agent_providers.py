from __future__ import annotations

from app.modules.agent_providers.service import list_agent_providers


def test_agent_provider_registry_keeps_codex_ready_and_gemini_foundation() -> None:
    response = list_agent_providers()
    providers = {provider.provider_id: provider for provider in response.providers}

    assert providers["codex"].status == "ready"
    assert providers["codex"].capabilities[0].protocol == "codex_chatgpt"
    assert providers["codex"].capabilities[0].proxyable is True

    gemini = providers["gemini"]
    assert gemini.status == "foundation"
    assert "api_key" in gemini.auth_modes
    assert any(capability.protocol == "gemini_api" and capability.proxyable for capability in gemini.capabilities)
    antigravity = next(capability for capability in gemini.capabilities if capability.protocol == "antigravity_cli")
    assert antigravity.proxyable is False
    assert antigravity.available_until == "2026-06-18"
    assert "agy harness" in antigravity.operator_action

    antigravity_provider = providers["antigravity"]
    assert antigravity_provider.status == "foundation"
    assert antigravity_provider.auth_modes == ["api_key", "cli_keyring"]
    managed = next(
        capability for capability in antigravity_provider.capabilities if capability.protocol == "interactions_api"
    )
    cli = next(
        capability for capability in antigravity_provider.capabilities if capability.protocol == "antigravity_cli"
    )
    assert managed.proxyable is True
    assert managed.status == "foundation"
    assert "antigravity-preview" in managed.operator_action
    assert cli.proxyable is False
    assert cli.status == "foundation"
    assert "agy --print" in cli.operator_action
