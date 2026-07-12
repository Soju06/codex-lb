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
