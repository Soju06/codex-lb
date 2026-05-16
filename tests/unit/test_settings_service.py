from __future__ import annotations

import pytest

from app.modules.settings.service import parse_additional_quota_routing_policies

pytestmark = pytest.mark.unit


def test_parse_additional_quota_routing_policies_returns_empty_for_malformed_json() -> None:
    assert parse_additional_quota_routing_policies("{not-json") == {}


def test_parse_additional_quota_routing_policies_normalizes_known_aliases() -> None:
    assert parse_additional_quota_routing_policies('{"codex_other": "preserve"}') == {
        "codex_spark": "preserve",
    }
