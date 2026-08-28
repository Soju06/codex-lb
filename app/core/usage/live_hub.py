from __future__ import annotations

from typing import Callable, Protocol

from app.core.usage.live_snapshots import LiveRateLimitSnapshot


class LiveUsagePublisher(Protocol):
    def __call__(
        self,
        snapshot: LiveRateLimitSnapshot,
        *,
        account_id: str | None = None,
        chatgpt_account_id: str | None = None,
    ) -> None: ...


# The core client layer publishes through this hub so it never imports the
# module layer; until startup registers an ingestor every publish is a no-op.
_publisher: Callable[..., None] | None = None


def register_live_usage_publisher(publisher: LiveUsagePublisher | None) -> None:
    global _publisher
    _publisher = publisher


def publish_live_usage(
    snapshot: LiveRateLimitSnapshot | None,
    *,
    account_id: str | None = None,
    chatgpt_account_id: str | None = None,
) -> None:
    if snapshot is None or not snapshot.has_windows:
        return
    if not account_id and not chatgpt_account_id:
        return
    publisher = _publisher
    if publisher is None:
        return
    publisher(snapshot, account_id=account_id, chatgpt_account_id=chatgpt_account_id)
