"""Bounded, process-local thread anchoring for unanchored Responses turns.

A Responses request that carries no continuity identifier (no
``conversation``, no ``previous_response_id``, no Codex session/thread header,
no client ``prompt_cache_key``) still belongs to a logical thread: every turn
resends the transcript, so consecutive turns overlap. This module recognises
that overlap so the proxy can keep one stable ``prompt_cache_key`` for the
thread instead of re-deriving a content hash that moves whenever the client
trims history or writes a volatile field into its first item.

Matching contract
-----------------
A turn extends an anchor **only** when the anchor's stored digest window is
consumed exactly by the head of the new turn's window::

    stored[offset:] == incoming[:len(stored) - offset]

with at least ``_MIN_OVERLAP_ITEMS`` items matched, or with the whole stored
window matched (``offset == 0``) when the thread's recorded body was shorter
than that. ``offset == 0`` is the append case -- and, when the two windows are
equal, an identical re-derivation of the same body, which must return the same
key. ``offset > 0`` is the leading-trim case: the client dropped the oldest
items and kept a contiguous recent window. Nothing else matches. A compacted or summarised turn does not
extend any stored window, so it mints a new anchor -- which is correct, because
the upstream prefix cache is genuinely cold after compaction. There is no
fuzzy, suffix-only, or "longest common prefix" fallback: partial evidence
merges unrelated threads onto one account, which is strictly worse than the
churn this module removes.

Item digests are domain-separated by (api key id, model class, **full**
instructions). Two threads whose instructions differ only after the first 512
characters -- which collide today -- therefore never share an anchor.

Memory bound
------------
All state is process-local and positive-only, mirroring the repository's
existing bounded TTL-LRU process cache shape:

* at most ``_MAX_ANCHORS`` (2048) anchors, each holding at most
  ``_MAX_WINDOW_ITEMS`` (32) x ``_ITEM_DIGEST_BYTES`` (8) = 256 bytes of
  digests plus its key string;
* at most ``_MAX_INDEX_DIGESTS`` (65536) reverse-index rows, each holding at
  most ``_MAX_CANDIDATES_PER_DIGEST`` (8) thread-key references.

Both are LRU-evicted ``OrderedDict``s, so the worst case is ~9 MiB per replica
(measured with every cap saturated) and never grows with traffic. Anchors also expire after the caller's
TTL (the dashboard ``openai_cache_affinity_max_age_seconds``) so an anchor can
never outlive the ``sticky_sessions`` row it names. Losing an anchor is always
safe: the next turn mints a new key and is reported as such.

State is per process. A restart, or a blue/green swap where both colors serve,
loses anchors: a thread can hold two keys and two owners for that window. That
is bounded by the TTL and costs one prefix-cache miss; it is never incorrect.
"""

from __future__ import annotations

import json
from collections import OrderedDict
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import sha256

from app.core.clock import REAL_CLOCK, Clock
from app.core.types import JsonValue

# Truncated SHA-256 per input item. 64 bits over a window that is additionally
# verified item-by-item and domain-separated per (api key, model, instructions).
_ITEM_DIGEST_BYTES = 8
# Trailing items retained per thread. Bounds both the memory per anchor and the
# leading-trim depth that can still be recognised.
_MAX_WINDOW_ITEMS = 32
# A body must carry at least one item to be anchorable at all.
_MIN_WINDOW_ITEMS = 1
# Two consecutive exactly-equal items are the least evidence accepted for a
# *partial* alignment. One item is not enough there: independent threads
# routinely share a single opening `<environment_context>` block or a one-word
# prompt, and accepting a one-item tail alignment would merge them. A single
# item is accepted only when it is the thread's whole recorded body and the new
# turn extends it from item zero, which is the ordinary second turn of a
# one-item opening.
_MIN_OVERLAP_ITEMS = 2
# Serialization ceilings, applied *during* encoding so a 450k-token item is
# never materialised. Items are hashed in full -- a truncated digest would
# re-introduce exactly the prefix collision this module removes -- so an item
# past the per-item ceiling makes the body unanchorable instead.
_MAX_ITEM_ENCODED_CHARS = 1024 * 1024
_MAX_WINDOW_ENCODED_CHARS = 256 * 1024
# LRU caps. See "Memory bound" above. Measured at ~9 MiB with every cap
# saturated; a replica serving the observed unanchored volume (16.6k requests
# per 10 h, 1800 s freshness window) stays far below the thread cap.
_MAX_ANCHORS = 2048
_MAX_CANDIDATES_PER_DIGEST = 8
_MAX_INDEX_DIGESTS = _MAX_ANCHORS * _MAX_WINDOW_ITEMS
# Index rows are capped, and a popular item -- one `<environment_context>`
# block shared by every session in a repository -- overflows its row. Probing
# the first few positions of the incoming window finds the thread through one
# of its rarer items instead. Candidates are only candidates: every one of
# them is still verified item by item.
_MAX_LOOKUP_PROBE_ITEMS = 8

# Same canonical encoding as ``_fingerprint_input_items``
# (app/modules/proxy/_service/response_create.py), applied per item so the
# window can be bounded without materialising the whole list.
_ITEM_ENCODER = json.JSONEncoder(ensure_ascii=True, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True, slots=True)
class ThreadWindow:
    """Chronological, bounded digest window over the tail of one input list."""

    digests: bytes
    item_count: int


@dataclass(slots=True)
class _Anchor:
    digests: bytes
    stored_at: float


def thread_anchor_domain(*, api_key_id: str, model_class: str, instructions: str) -> bytes:
    """Domain separator mixed into every item digest.

    Length framing keeps distinct tuples distinct even when a component
    contains the separator. ``instructions`` is hashed in full, not truncated:
    two Codex system prompts that diverge only past 512 characters are
    different threads.
    """

    parts = (api_key_id.encode(), model_class.encode(), instructions.encode())
    framed = b"".join(len(part).to_bytes(8, "big") + part for part in parts)
    return sha256(framed).digest()


def _bounded_item_digest(domain: bytes, item: JsonValue) -> tuple[bytes, int] | None:
    """Digest one item's full canonical JSON, or ``None`` if it is unusable.

    ``iterencode`` yields chunks lazily and each chunk is folded straight into
    the hash, so the encoding is never materialised. ``None`` means the item
    exceeds ``_MAX_ITEM_ENCODED_CHARS`` or the canonical encoder cannot
    represent it; the body is then reported unanchorable rather than anchored
    on a partial or non-deterministic encoding.
    """

    hasher = sha256(domain + b"\x1e")
    size = 0
    try:
        for chunk in _ITEM_ENCODER.iterencode(item):
            size += len(chunk)
            if size > _MAX_ITEM_ENCODED_CHARS:
                return None
            hasher.update(chunk.encode())
    except (TypeError, ValueError):
        return None
    return hasher.digest()[:_ITEM_DIGEST_BYTES], size


def build_thread_window(input_value: JsonValue, *, domain: bytes) -> ThreadWindow | None:
    """Digest the trailing items of ``input_value``, or ``None`` if unanchorable.

    Only a list of items is anchorable: discrete items are what make "the
    client appended a turn" distinguishable from "this is a different thread
    that happens to share a prefix". ``ResponsesRequest`` already normalises a
    bare string input into a one-item list, so the non-list branch is
    defensive.
    """

    if not isinstance(input_value, list):
        return None
    items: Sequence[JsonValue] = input_value
    if len(items) < _MIN_WINDOW_ITEMS:
        return None
    digests: list[bytes] = []
    encoded_chars = 0
    for item in reversed(items):
        if len(digests) >= _MAX_WINDOW_ITEMS:
            break
        if encoded_chars >= _MAX_WINDOW_ENCODED_CHARS and len(digests) >= _MIN_WINDOW_ITEMS:
            break
        digested = _bounded_item_digest(domain, item)
        if digested is None:
            return None
        digest, encoded_size = digested
        encoded_chars += encoded_size
        digests.append(digest)
    if len(digests) < _MIN_WINDOW_ITEMS:
        return None
    digests.reverse()
    return ThreadWindow(digests=b"".join(digests), item_count=len(digests))


def _overlap_items(stored: bytes, incoming: bytes) -> int:
    """Items matched when ``stored``'s suffix is consumed by ``incoming``'s head.

    Offsets ascend, so the first match is the deepest one.
    """

    for offset in range(0, len(stored), _ITEM_DIGEST_BYTES):
        suffix_length = len(stored) - offset
        if suffix_length > len(incoming):
            continue
        if stored[offset:] == incoming[:suffix_length]:
            return suffix_length // _ITEM_DIGEST_BYTES
    return 0


class ThreadAnchorIndex:
    """Positive-only, TTL- and LRU-bounded index from digest window to thread key.

    Not synchronised: the proxy resolves affinity synchronously inside one
    event-loop task, so no await point can interleave two mutations.
    """

    def __init__(
        self,
        *,
        max_anchors: int = _MAX_ANCHORS,
        max_index_digests: int = _MAX_INDEX_DIGESTS,
        max_candidates_per_digest: int = _MAX_CANDIDATES_PER_DIGEST,
        clock: Clock = REAL_CLOCK,
    ) -> None:
        if max_anchors <= 0 or max_index_digests <= 0 or max_candidates_per_digest <= 0:
            raise ValueError("thread anchor bounds must be positive")
        self._clock = clock
        self._max_anchors = max_anchors
        self._max_index_digests = max_index_digests
        self._max_candidates_per_digest = max_candidates_per_digest
        self._anchors: OrderedDict[str, _Anchor] = OrderedDict()
        self._index: OrderedDict[bytes, list[str]] = OrderedDict()

    def __len__(self) -> int:
        return len(self._anchors)

    @property
    def index_size(self) -> int:
        return len(self._index)

    def clear(self) -> None:
        self._anchors.clear()
        self._index.clear()

    def lookup(self, window: ThreadWindow, *, ttl_seconds: float) -> str | None:
        """Return the thread key this window verifiably extends, if any.

        The index is only a candidate source; the returned key is always
        confirmed by an exact item-digest comparison, and the deepest verified
        overlap wins.
        """

        now = self._clock.monotonic()
        best_key: str | None = None
        best_overlap = 0
        probes = min(window.item_count, _MAX_LOOKUP_PROBE_ITEMS)
        seen: set[str] = set()
        for probe in range(probes):
            offset = probe * _ITEM_DIGEST_BYTES
            digest = window.digests[offset : offset + _ITEM_DIGEST_BYTES]
            candidates = self._index.get(digest)
            if not candidates:
                continue
            self._index.move_to_end(digest)
            for thread_key in list(candidates):
                anchor = self._anchors.get(thread_key)
                if anchor is None or now - anchor.stored_at >= ttl_seconds:
                    self._anchors.pop(thread_key, None)
                    candidates.remove(thread_key)
                    continue
                if thread_key in seen:
                    continue
                seen.add(thread_key)
                overlap = _overlap_items(anchor.digests, window.digests)
                accepted = overlap >= _MIN_OVERLAP_ITEMS or (
                    overlap > 0 and overlap * _ITEM_DIGEST_BYTES == len(anchor.digests)
                )
                if accepted and overlap > best_overlap:
                    best_key = thread_key
                    best_overlap = overlap
            if not candidates:
                del self._index[digest]
        if best_key is not None:
            self._anchors.move_to_end(best_key)
        return best_key

    def register(self, thread_key: str, window: ThreadWindow) -> None:
        """Record this turn's window as the thread's anchor and index it."""

        self._anchors[thread_key] = _Anchor(digests=window.digests, stored_at=self._clock.monotonic())
        self._anchors.move_to_end(thread_key)
        while len(self._anchors) > self._max_anchors:
            self._anchors.popitem(last=False)
        for offset in range(0, len(window.digests), _ITEM_DIGEST_BYTES):
            digest = window.digests[offset : offset + _ITEM_DIGEST_BYTES]
            row = self._index.get(digest)
            if row is None:
                row = []
                self._index[digest] = row
            elif thread_key in row:
                row.remove(thread_key)
            row.append(thread_key)
            del row[: -self._max_candidates_per_digest]
            self._index.move_to_end(digest)
        while len(self._index) > self._max_index_digests:
            self._index.popitem(last=False)


_THREAD_ANCHOR_INDEX: ThreadAnchorIndex | None = None


def get_thread_anchor_index() -> ThreadAnchorIndex:
    """Process-wide anchor index (one per replica worker)."""

    global _THREAD_ANCHOR_INDEX
    if _THREAD_ANCHOR_INDEX is None:
        _THREAD_ANCHOR_INDEX = ThreadAnchorIndex()
    return _THREAD_ANCHOR_INDEX


def reset_thread_anchor_index() -> None:
    """Drop all anchors. Test helper; safe at runtime (anchors are a cache)."""

    get_thread_anchor_index().clear()
