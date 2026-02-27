from __future__ import annotations

import json

import pytest

from app.core.auth.anthropic_credentials import (
    clear_anthropic_credentials_cache,
    resolve_anthropic_credentials,
)
from app.core.config.settings import get_settings

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_resolve_anthropic_credentials_prefers_env(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_ANTHROPIC_USAGE_BEARER_TOKEN", "sk-ant-oat-example-env-token")
    monkeypatch.setenv("CODEX_LB_ANTHROPIC_ORG_ID", "org_env")
    monkeypatch.setenv("CODEX_LB_ANTHROPIC_CREDENTIALS_DISCOVERY_ENABLED", "false")
    get_settings.cache_clear()
    clear_anthropic_credentials_cache()

    credentials = await resolve_anthropic_credentials(force_refresh=True)

    assert credentials is not None
    assert credentials.source == "env"
    assert credentials.bearer_token == "sk-ant-oat-example-env-token"
    assert credentials.org_id == "org_env"


@pytest.mark.asyncio
async def test_resolve_anthropic_credentials_reads_config_file(tmp_path, monkeypatch) -> None:
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "session": {
                    "oauth": {
                        "token": "sk-ant-oat-example-file-token",
                        "organization_id": "org_file",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("CODEX_LB_ANTHROPIC_USAGE_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("CODEX_LB_ANTHROPIC_ORG_ID", raising=False)
    monkeypatch.setattr("app.core.auth.anthropic_credentials._is_linux", lambda: True)
    monkeypatch.setenv("CODEX_LB_ANTHROPIC_CREDENTIALS_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_ANTHROPIC_AUTO_DISCOVER_ORG", "false")
    monkeypatch.setenv("CODEX_LB_ANTHROPIC_CREDENTIALS_FILE", str(credentials_file))
    get_settings.cache_clear()
    clear_anthropic_credentials_cache()

    credentials = await resolve_anthropic_credentials(force_refresh=True)

    assert credentials is not None
    assert credentials.source.startswith("file:")
    assert credentials.bearer_token == "sk-ant-oat-example-file-token"
    assert credentials.org_id == "org_file"
