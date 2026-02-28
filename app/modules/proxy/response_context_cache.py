from __future__ import annotations

import time
from collections import OrderedDict
from copy import deepcopy
from dataclasses import dataclass
from threading import Lock

from app.core.types import JsonValue
from app.core.utils.json_guards import is_json_list, is_json_mapping


@dataclass(slots=True)
class _CachedResponse:
    output: list[JsonValue]
    expires_at: float


class ResponseContextCache:
    def __init__(
        self,
        *,
        ttl_seconds: int = 6 * 60 * 60,
        max_responses: int = 1024,
        max_items: int = 4096,
    ) -> None:
        self._ttl_seconds = ttl_seconds
        self._max_responses = max_responses
        self._max_items = max_items
        self._responses: OrderedDict[str, _CachedResponse] = OrderedDict()
        self._items: OrderedDict[str, tuple[JsonValue, float]] = OrderedDict()
        self._lock = Lock()

    def clear(self) -> None:
        with self._lock:
            self._responses.clear()
            self._items.clear()

    def configure(
        self,
        *,
        ttl_seconds: int,
        max_responses: int,
        max_items: int,
    ) -> None:
        if ttl_seconds <= 0 or max_responses <= 0 or max_items <= 0:
            return
        with self._lock:
            self._ttl_seconds = ttl_seconds
            self._max_responses = max_responses
            self._max_items = max_items
            self._prune(time.monotonic())

    def store_response(self, response_payload: dict[str, JsonValue]) -> None:
        response_id = response_payload.get("id")
        output = response_payload.get("output")
        if not isinstance(response_id, str) or not response_id or not is_json_list(output):
            return

        output_copy = [deepcopy(item) for item in output]
        now = time.monotonic()
        expires_at = now + self._ttl_seconds

        with self._lock:
            self._prune(now)
            self._responses[response_id] = _CachedResponse(output=output_copy, expires_at=expires_at)
            self._responses.move_to_end(response_id)

            for item in output_copy:
                if not is_json_mapping(item):
                    continue
                item_id = item.get("id")
                if isinstance(item_id, str) and item_id:
                    self._items[item_id] = (deepcopy(item), expires_at)
                    self._items.move_to_end(item_id)

            self._prune(now)

    def resolve_reference(self, reference_id: str) -> list[JsonValue] | None:
        now = time.monotonic()
        with self._lock:
            self._prune(now)

            cached_response = self._responses.get(reference_id)
            if cached_response is not None:
                self._responses.move_to_end(reference_id)
                return [deepcopy(item) for item in cached_response.output]

            cached_item = self._items.get(reference_id)
            if cached_item is not None:
                item, _expires_at = cached_item
                self._items.move_to_end(reference_id)
                return [deepcopy(item)]

        return None

    def _prune(self, now: float) -> None:
        expired_response_ids = [rid for rid, cached in self._responses.items() if cached.expires_at <= now]
        for response_id in expired_response_ids:
            self._responses.pop(response_id, None)

        expired_item_ids = [item_id for item_id, (_item, expires_at) in self._items.items() if expires_at <= now]
        for item_id in expired_item_ids:
            self._items.pop(item_id, None)

        while len(self._responses) > self._max_responses:
            self._responses.popitem(last=False)
        while len(self._items) > self._max_items:
            self._items.popitem(last=False)


_response_context_cache = ResponseContextCache()


def get_response_context_cache() -> ResponseContextCache:
    return _response_context_cache
