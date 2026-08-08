from __future__ import annotations

from uuid import uuid4

_HTTP_BRIDGE_OWNER_PROCESS_EPOCH = uuid4().hex


def http_bridge_owner_process_epoch() -> str:
    return _HTTP_BRIDGE_OWNER_PROCESS_EPOCH
