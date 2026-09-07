from __future__ import annotations

import asyncio
import sys
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(slots=True)
class BufferBudget:
    limit: int
    used: int = 0


class BufferFull(asyncio.QueueFull):
    def __init__(self, connection: BufferBudget, shared: BufferBudget, incoming: int) -> None:
        super().__init__("native websocket queue byte budget exceeded")
        self.detail = (
            f"connection_queue_bytes={connection.used};connection_limit_bytes={connection.limit};"
            f"helper_queue_bytes={shared.used};helper_limit_bytes={shared.limit};incoming_bytes={incoming}"
        )


def event_size(value: object) -> int:
    """Count JSON event allocations conservatively, including container overhead."""
    size = sys.getsizeof(value)
    if isinstance(value, dict):
        return size + sum(event_size(key) + event_size(item) for key, item in value.items())
    if isinstance(value, (list, tuple)):
        return size + sum(event_size(item) for item in value)
    return size


class ByteQueue[T](asyncio.Queue[T]):
    """Queue stages share connection/helper budgets without blocking controls.

    Terminal exceptions use a reserved uncharged slot so exhausted data capacity
    cannot hide failure. Callers publish at most one terminal per queue.
    """

    def __init__(self, connection: BufferBudget, shared: BufferBudget, size: Callable[[T], int]) -> None:
        super().__init__()
        self.connection = connection
        self.shared = shared
        self._size = size
        self._charges: deque[int] = deque()

    def put_nowait(self, item: T) -> None:
        # Include deque references and charge bookkeeping for tiny messages.
        size = 0 if isinstance(item, BaseException) else self._size(item)
        charge = size + 64 if size else 0
        if charge and (
            self.connection.used + charge > self.connection.limit or self.shared.used + charge > self.shared.limit
        ):
            raise BufferFull(self.connection, self.shared, charge)
        super().put_nowait(item)
        self._charges.append(charge)
        self.connection.used += charge
        self.shared.used += charge

    def get_nowait(self) -> T:
        item = super().get_nowait()
        charge = self._charges.popleft()
        self.connection.used -= charge
        self.shared.used -= charge
        return item

    def clear(self) -> None:
        while not self.empty():
            self.get_nowait()

    def __del__(self) -> None:
        # A finished socket can be abandoned by its owner without close().
        # Its queue charges must not permanently consume shared capacity.
        charge = sum(self._charges)
        self.connection.used -= charge
        self.shared.used -= charge
