from __future__ import annotations

import pytest

from app.core.clients.proxy import ProxyResponseError
from app.core.errors import openai_error
from app.modules.proxy.service import _should_failover_previsible_unary_proxy_error

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("phase", "retryable", "code", "detail", "message", "expected"),
    [
        ("connect", True, "upstream_unavailable", "transport_error", "ClientProxyConnectionError", True),
        ("connect", False, "upstream_unavailable", "transport_error", "connection reset", False),
        ("body_read", True, "upstream_unavailable", "transport_error", "connection reset", False),
        ("connect", True, "proxy_network_unavailable", "transport_error", "connection reset", False),
        ("connect", False, "upstream_unavailable", None, "connection reset", True),
        ("connect", False, "upstream_unavailable", None, "certificate verify failed", False),
    ],
)
def test_unary_failover_observes_typed_provenance(phase, retryable, code, detail, message, expected):
    error = ProxyResponseError(
        502,
        openai_error(code, message),
        failure_phase=phase,
        retryable_same_contract=retryable,
        failure_detail=detail,
    )
    assert _should_failover_previsible_unary_proxy_error(error) is expected
