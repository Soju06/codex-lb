import pytest

from app.core.resilience.overload import LOCAL_OVERLOAD_CODES
from app.modules.proxy.load_balancer import AccountSelection
from app.modules.proxy.selection_errors import selection_failure_response


def test_pool_usage_exhaustion_is_codex_compatible_429():
    status, payload = selection_failure_response(
        AccountSelection(
            account=None,
            error_message="Usage limit reached",
            error_code="usage_limit_reached",
        )
    )

    assert status == 429
    assert payload == {
        "error": {
            "message": "Usage limit reached",
            "type": "usage_limit_reached",
            "code": "usage_limit_reached",
        }
    }


def test_unusable_pool_remains_no_accounts_503():
    status, payload = selection_failure_response(
        AccountSelection(
            account=None,
            error_message="All accounts require re-authentication",
            error_code=None,
        )
    )

    assert status == 503
    assert payload["error"]["type"] == "server_error"
    assert payload["error"]["code"] == "no_accounts"


def test_pool_usage_exhaustion_preserves_authoritative_reset():
    status, payload = selection_failure_response(
        AccountSelection(
            account=None,
            error_message="Rate limit exceeded. Try again in 300s",
            error_code="usage_limit_reached",
            resets_at=1_700_003_600,
        )
    )

    assert status == 429
    assert payload["error"]["resets_at"] == 1_700_003_600


@pytest.mark.parametrize("local_code", sorted(LOCAL_OVERLOAD_CODES))
def test_local_overload_codes_keep_rate_limit_contract(local_code: str):
    # Covers every canonical local capacity code, including codes added later
    # (e.g. api_key_stream_fair_share): local overload must stay a 429
    # rate_limit_error and never be reclassified as upstream usage exhaustion
    # or a 503.
    status, payload = selection_failure_response(
        AccountSelection(
            account=None,
            error_message="Local capacity is exhausted",
            error_code=local_code,
        )
    )

    assert status == 429
    assert payload["error"]["type"] == "rate_limit_error"
    assert payload["error"]["code"] == local_code
    assert "resets_at" not in payload["error"]
