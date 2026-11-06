from typing import Any, cast

import pytest

from app.core.clients.native_egress import NativeEgressTransportError
from app.core.clients.proxy_websocket import NativeUpstreamWebSocket


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "phase",
    [
        "consumer_backpressure",
        "helper_exit",
        "helper_read",
        "websocket_receive",
        "liveness_timeout",
        "timeout",
        "unknown-secret-phase",
    ],
)
async def test_native_receive_preserves_only_safe_failure_phase(phase: str) -> None:
    class FailingSocket:
        async def receive(self) -> Any:
            raise NativeEgressTransportError("credential-bearing helper detail", failure_phase=phase)

    message = await NativeUpstreamWebSocket(cast(Any, FailingSocket())).receive()
    assert message.failure_phase == (None if phase == "unknown-secret-phase" else phase)
    assert message.error == "Upstream websocket receive failed"
    assert "credential-bearing" not in str(message)
