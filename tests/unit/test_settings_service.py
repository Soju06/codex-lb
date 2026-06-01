from __future__ import annotations

import json

import pytest

from app.modules.settings.service import (
    _dump_additional_quota_routing_policies,
    _parse_additional_quota_routing_policies,
)

pytestmark = pytest.mark.unit


def test_parse_additional_quota_routing_policies_normalizes_aliases_and_policy_case() -> None:
    raw = json.dumps(
        {
            "codex-spark": "burn_first",
            "codex_spark": " preserve ",
            "gpt-5.3-codex-spark": "normal",
            "other": "legacy",
            123: "preserve",
        }
    )

    parsed = _parse_additional_quota_routing_policies(raw)
    assert parsed == {
        "codex_spark": "normal",
    }


def test_parse_additional_quota_routing_policies_handles_invalid_json() -> None:
    assert _parse_additional_quota_routing_policies(None) == {}
    assert _parse_additional_quota_routing_policies("not-json") == {}


def test_dump_additional_quota_routing_policies_canonicalizes_keys_and_filters_invalid() -> None:
    dumped = _dump_additional_quota_routing_policies(
        {
            "codex-spark": "normal",
            "codex_spark": "preserve",
            "  gpt-5.3-codex-spark  ": "burn_first",
            "bad-key": "normal",
        }
    )
    assert json.loads(dumped) == {"codex_spark": "burn_first"}
