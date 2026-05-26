"""Unit tests for the per-account SOCKS5 proxy Pydantic schemas."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.modules.accounts.schemas import AccountProxyInput, AccountProxySummary


def _proxy_auth_fixture(suffix: str = "primary") -> str:
    return f"proxy-fixture-value-{suffix}"


def test_account_proxy_input_accepts_minimal_payload() -> None:
    payload = AccountProxyInput.model_validate({"host": "proxy.example.com", "port": 1080})
    assert payload.host == "proxy.example.com"
    assert payload.port == 1080
    assert payload.username is None
    assert payload.password is None
    assert payload.remote_dns is True
    assert payload.label is None
    assert payload.clear_password is False


def test_account_proxy_input_accepts_full_payload_via_camel_case() -> None:
    payload = AccountProxyInput.model_validate(
        {
            "host": "  proxy.example.com  ",
            "port": 1085,
            "username": "user",
            "password": _proxy_auth_fixture(),
            "remoteDns": False,
            "label": "house-1",
        }
    )
    assert payload.host == "proxy.example.com"  # whitespace trimmed
    assert payload.port == 1085
    assert payload.username == "user"
    assert payload.password == _proxy_auth_fixture()
    assert payload.remote_dns is False
    assert payload.label == "house-1"


def test_account_proxy_input_rejects_blank_or_whitespace_host() -> None:
    with pytest.raises(ValidationError):
        AccountProxyInput.model_validate({"host": "", "port": 1080})
    with pytest.raises(ValidationError):
        AccountProxyInput.model_validate({"host": "   ", "port": 1080})


@pytest.mark.parametrize("bad_port", [0, -1, 65536, 70000])
def test_account_proxy_input_rejects_out_of_range_port(bad_port: int) -> None:
    with pytest.raises(ValidationError):
        AccountProxyInput.model_validate({"host": "proxy.example.com", "port": bad_port})


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_account_proxy_input_normalizes_blank_optional_fields_to_none(blank: str) -> None:
    payload = AccountProxyInput.model_validate(
        {"host": "proxy.example.com", "port": 1080, "username": blank, "password": blank, "label": blank}
    )
    assert payload.username is None
    assert payload.password is None
    assert payload.label is None


def test_account_proxy_input_preserves_non_blank_password_whitespace() -> None:
    payload = AccountProxyInput.model_validate(
        {"host": "proxy.example.com", "port": 1080, "password": f" {_proxy_auth_fixture('spaced')} "}
    )
    assert payload.password == f" {_proxy_auth_fixture('spaced')} "


def test_account_proxy_input_accepts_explicit_clear_password() -> None:
    payload = AccountProxyInput.model_validate(
        {
            "host": "proxy.example.com",
            "port": 1080,
            "password": None,
            "clearPassword": True,
        }
    )
    assert payload.password is None
    assert payload.clear_password is True


def test_account_proxy_summary_serializes_without_password() -> None:
    summary = AccountProxySummary(
        host="proxy.example.com",
        port=1080,
        username="user",
        has_password=True,
        remote_dns=False,
        label="house-1",
        last_validated_at=datetime(2026, 5, 23, 12, 0, tzinfo=timezone.utc),
    )
    json_payload = summary.model_dump(mode="json", by_alias=True)
    assert "password" not in json_payload
    assert "passwordEncrypted" not in json_payload
    assert json_payload["hasPassword"] is True
    assert json_payload["host"] == "proxy.example.com"
    assert json_payload["port"] == 1080
    assert json_payload["remoteDns"] is False
    assert json_payload["lastValidatedAt"].endswith("Z")
