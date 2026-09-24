from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.core.balancer import failover_decision
from app.core.usage.models import UsagePayload
from app.modules.proxy.additional_model_limits import get_additional_model_limit
from app.modules.proxy.helpers import classify_upstream_failure
from app.modules.proxy.load_balancer import _additional_quota_routing_policy_override
from app.modules.usage.additional_quota_keys import (
    ROUTING_POLICY_DISABLED,
    additional_quota_display_label,
    additional_quota_routing_disabled,
    canonicalize_additional_quota_key,
    get_additional_quota_definition_for_model,
    get_additional_quota_routing_policy,
)

pytestmark = pytest.mark.unit

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "upstream_usage_with_luna_reserve.json"
_RESERVE_QUOTA_KEY = "base_model_inference"


def _reserve_payload() -> UsagePayload:
    """Parse the captured upstream body the way the usage fetch does.

    Going through ``model_validate`` on real JSON is the point of this helper:
    constructing ``UsagePayload(...)`` in Python skips key resolution, so a
    wire field named differently than the model would still pass.
    """
    with _FIXTURE.open(encoding="utf-8") as handle:
        body = json.load(handle)
    body.pop("_fixture_notes", None)
    return UsagePayload.model_validate(body)


class TestReservePayloadParsing:
    def test_reserve_bucket_survives_json_parsing(self) -> None:
        payload = _reserve_payload()

        assert payload.additional_rate_limits is not None
        buckets = [entry for entry in payload.additional_rate_limits if entry.metered_feature == _RESERVE_QUOTA_KEY]
        assert len(buckets) == 1
        assert buckets[0].limit_name == "gpt-reserve"

    def test_reserve_window_is_read_from_the_parsed_body(self) -> None:
        payload = _reserve_payload()
        assert payload.additional_rate_limits is not None
        bucket = payload.additional_rate_limits[0]

        assert bucket.rate_limit is not None
        assert bucket.rate_limit.primary_window is not None
        assert bucket.rate_limit.primary_window.used_percent == 0.0
        assert bucket.rate_limit.secondary_window is None

    def test_parsed_bucket_resolves_to_the_registry_entry(self) -> None:
        payload = _reserve_payload()
        assert payload.additional_rate_limits is not None
        bucket = payload.additional_rate_limits[0]

        assert canonicalize_additional_quota_key(metered_feature=bucket.metered_feature) == _RESERVE_QUOTA_KEY
        assert canonicalize_additional_quota_key(limit_name=bucket.limit_name) == _RESERVE_QUOTA_KEY


class TestReserveRegistryEntry:
    def test_gpt_reserve_maps_to_the_reserve_quota(self) -> None:
        resolved = get_additional_model_limit("gpt-reserve")

        assert resolved is not None
        assert resolved.quota_key == _RESERVE_QUOTA_KEY
        assert resolved.display_label == "Luna Reserve"

    def test_reserve_applies_to_plus_and_pro(self) -> None:
        definition = get_additional_quota_definition_for_model("gpt-reserve")

        assert definition is not None
        assert definition.applies_to_plans == frozenset({"plus", "pro"})

    def test_reserve_ships_disabled(self) -> None:
        assert get_additional_quota_routing_policy(_RESERVE_QUOTA_KEY) == ROUTING_POLICY_DISABLED

    def test_disabled_is_not_coerced_to_inherit_by_the_registry_loader(self) -> None:
        definition = get_additional_quota_definition_for_model("gpt-reserve")

        assert definition is not None
        assert definition.routing_policy == ROUTING_POLICY_DISABLED

    def test_existing_spark_quota_is_untouched(self) -> None:
        assert get_additional_quota_routing_policy("codex_spark") == "burn_first"


class TestRoutingGate:
    def test_reserve_routing_is_off_without_an_override(self) -> None:
        assert additional_quota_routing_disabled("gpt-reserve", {}) is True

    def test_operator_override_turns_reserve_routing_on(self) -> None:
        assert additional_quota_routing_disabled("gpt-reserve", {_RESERVE_QUOTA_KEY: "normal"}) is False

    def test_operator_can_switch_reserve_back_off(self) -> None:
        assert additional_quota_routing_disabled("gpt-reserve", {_RESERVE_QUOTA_KEY: "disabled"}) is True

    def test_spark_is_not_gated_by_the_reserve_policy(self) -> None:
        assert additional_quota_routing_disabled("gpt-5.3-codex-spark", {}) is False

    def test_disabled_never_leaks_into_the_account_ranking_policy(self) -> None:
        # AccountState.routing_policy only accepts account-level policies, so a
        # bucket set to ``disabled`` must produce no ranking override at all.
        assert _additional_quota_routing_policy_override("gpt-reserve", {}) is None

    def test_an_enabled_reserve_still_yields_its_ranking_policy(self) -> None:
        override = _additional_quota_routing_policy_override("gpt-reserve", {_RESERVE_QUOTA_KEY: "preserve"})

        assert override == "preserve"

    def test_display_label_is_used_for_the_refusal_message(self) -> None:
        assert additional_quota_display_label("gpt-reserve") == "Luna Reserve"


class TestIneligibleAccountRejection:
    def test_an_unrecognised_reserve_refusal_is_not_retried_across_accounts(self) -> None:
        classified = classify_upstream_failure(
            error_code="model_not_available",
            error={"message": "This account is not eligible for gpt-reserve."},
            http_status=400,
            phase="connect",
        )

        assert classified["failure_class"] == "non_retryable"

    def test_a_non_retryable_reserve_refusal_surfaces_instead_of_failing_over(self) -> None:
        action = failover_decision(
            failure_class="non_retryable",
            downstream_visible=False,
            candidates_remaining=3,
        )

        assert action == "surface"
