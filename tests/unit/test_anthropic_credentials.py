from __future__ import annotations

import json

import pytest

from app.core.auth.anthropic_credentials import (
    clear_anthropic_credentials_cache,
    credentials_from_account,
    parse_anthropic_auth_json,
    resolve_anthropic_credentials,
)
from app.core.config.settings import get_settings
from app.core.crypto import TokenEncryptor
from app.core.utils.time import utcnow
from app.db.models import Account, AccountStatus

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_resolve_anthropic_credentials_prefers_env(monkeypatch) -> None:
    monkeypatch.setenv("CODEX_LB_ANTHROPIC_USAGE_BEARER_TOKEN", "sk-ant-oat-test-env-token")
    monkeypatch.setenv("CODEX_LB_ANTHROPIC_ORG_ID", "org_env")
    monkeypatch.setenv("CODEX_LB_ANTHROPIC_CREDENTIALS_DISCOVERY_ENABLED", "false")
    get_settings.cache_clear()
    clear_anthropic_credentials_cache()

    credentials = await resolve_anthropic_credentials(force_refresh=True)

    assert credentials is not None
    assert credentials.source == "env"
    assert credentials.bearer_token == "sk-ant-oat-test-env-token"
    assert credentials.org_id == "org_env"


@pytest.mark.asyncio
async def test_resolve_anthropic_credentials_reads_config_file(tmp_path, monkeypatch) -> None:
    credentials_file = tmp_path / "credentials.json"
    credentials_file.write_text(
        json.dumps(
            {
                "session": {
                    "oauth": {
                        "token": "sk-ant-oat-test-file-token",
                        "organization_id": "org_file",
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.delenv("CODEX_LB_ANTHROPIC_USAGE_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("CODEX_LB_ANTHROPIC_ORG_ID", raising=False)
    monkeypatch.setenv("CODEX_LB_ANTHROPIC_CREDENTIALS_DISCOVERY_ENABLED", "true")
    monkeypatch.setenv("CODEX_LB_ANTHROPIC_AUTO_DISCOVER_ORG", "false")
    monkeypatch.setenv("CODEX_LB_ANTHROPIC_CREDENTIALS_FILE", str(credentials_file))
    get_settings.cache_clear()
    clear_anthropic_credentials_cache()

    credentials = await resolve_anthropic_credentials(force_refresh=True)

    assert credentials is not None
    assert credentials.source.startswith("file:")
    assert credentials.bearer_token == "sk-ant-oat-test-file-token"
    assert credentials.org_id == "org_file"


def test_parse_anthropic_auth_json_extracts_tokens_and_email() -> None:
    payload = {
        "claudeAiOauth": {
            "accessToken": "sk-ant-oat-test-access",
            "refreshToken": "refresh-test",
            "expiresAt": 1_893_456_789_000,
        },
        "user": {"email": "claude@example.com"},
    }

    parsed = parse_anthropic_auth_json(json.dumps(payload).encode("utf-8"))

    assert parsed.access_token == "sk-ant-oat-test-access"
    assert parsed.refresh_token == "refresh-test"
    assert parsed.email == "claude@example.com"


def test_credentials_from_account_uses_valid_anthropic_token() -> None:
    encryptor = TokenEncryptor()
    account = Account(
        id="anthropic_default",
        chatgpt_account_id=None,
        email="claude@example.com",
        plan_type="pro",
        access_token_encrypted=encryptor.encrypt("sk-ant-oat-test-access"),
        refresh_token_encrypted=encryptor.encrypt("refresh-test"),
        id_token_encrypted=encryptor.encrypt(""),
        last_refresh=utcnow(),
        status=AccountStatus.ACTIVE,
        deactivation_reason=None,
    )

    credentials = credentials_from_account(account)

    assert credentials is not None
    assert credentials.bearer_token == "sk-ant-oat-test-access"
    assert credentials.refresh_token == "refresh-test"
