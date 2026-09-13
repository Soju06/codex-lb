"""Settings fields promoted from ad-hoc ``os.environ`` reads (slop-removal 0908).

``CODEX_LB_CONNECT_ADDRESS``, ``CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE`` and
``FORWARDED_ALLOW_IPS`` used to be read directly by request/registry code. They
are now ``Settings`` fields so env files, the reference generator, and the
removed-settings warning govern them. These tests pin the env-name contract
and the blank-value semantics each consumer relied on.
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest import mock

import pytest

from app.core.config.settings import Settings

pytestmark = pytest.mark.unit

_PROMOTED = (
    "FORWARDED_ALLOW_IPS",
    "CODEX_LB_FORWARDED_ALLOW_IPS",
    "CODEX_LB_CONNECT_ADDRESS",
    "CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE",
)


def _settings(**env: str) -> Settings:
    clean = {k: v for k, v in os.environ.items() if k not in _PROMOTED}
    clean.update(env)
    with mock.patch.dict(os.environ, clean, clear=True):
        return Settings(_env_file=None)


def test_promoted_fields_default_to_unset() -> None:
    settings = _settings()
    assert settings.forwarded_allow_ips is None
    assert settings.connect_address is None
    assert settings.additional_quota_registry_file is None


@pytest.mark.parametrize(
    ("env_name", "value", "expected"),
    [
        ("FORWARDED_ALLOW_IPS", "10.0.0.0/8, 127.0.0.1", "10.0.0.0/8, 127.0.0.1"),
        ("FORWARDED_ALLOW_IPS", "*", "*"),
        # Empty means "trust no peer" for Uvicorn; it must not collapse to unset.
        ("FORWARDED_ALLOW_IPS", "", ""),
        ("CODEX_LB_FORWARDED_ALLOW_IPS", "192.0.2.1", "192.0.2.1"),
    ],
)
def test_forwarded_allow_ips_keeps_uvicorn_env_contract(env_name: str, value: str, expected: str) -> None:
    assert _settings(**{env_name: value}).forwarded_allow_ips == expected


def test_forwarded_allow_ips_bare_name_wins_over_prefixed_alias() -> None:
    settings = _settings(FORWARDED_ALLOW_IPS="10.0.0.1", CODEX_LB_FORWARDED_ALLOW_IPS="*")
    assert settings.forwarded_allow_ips == "10.0.0.1"


def test_connect_address_strips_and_blank_is_unset() -> None:
    assert _settings(CODEX_LB_CONNECT_ADDRESS="  lb.internal:2455  ").connect_address == "lb.internal:2455"
    assert _settings(CODEX_LB_CONNECT_ADDRESS="   ").connect_address is None


def test_additional_quota_registry_file_is_path_and_blank_is_unset() -> None:
    settings = _settings(CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE=" /etc/codex-lb/quota.json ")
    assert settings.additional_quota_registry_file == Path("/etc/codex-lb/quota.json")
    assert _settings(CODEX_LB_ADDITIONAL_QUOTA_REGISTRY_FILE="").additional_quota_registry_file is None
