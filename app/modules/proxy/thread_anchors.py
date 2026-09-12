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

and **either** at least ``_MIN_OVERLAP_ITEMS`` items are matched, **or** the
two windows are byte-equal (an identical re-derivation of the same body, which
must return the same key). Nothing else matches.

``offset > 0`` is the append/leading-trim case: the window slid forward because
the client appended a turn, or dropped its oldest items and kept a contiguous
recent window. ``offset == 0`` with the incoming window strictly longer is the
append case for a thread whose transcript is still shorter than the retained
window.

The ``_MIN_OVERLAP_ITEMS`` floor is the whole false-merge guard, and it applies
to the ``offset == 0`` case too. A short stored window that is a *prefix* of an
incoming window is structurally indistinguishable from "the same thread
appended two items": the only defence is to require the shared run to be long
enough that it cannot be a generic opening. One or two shared leading items
routinely *are* generic -- every session in a repository opens with the same
``<environment_context>`` block, and parallel workers of one agent open with a
byte-identical preamble -- so accepting that evidence would hand one thread's
key to another and then (via ``register``) overwrite the first thread's window,
costing it the key it owns. Requiring four keeps both named shapes out with two
items of margin. The price is that a thread does not hold a key until its
transcript reaches four items; at two items per turn that is one extra mint, on
the smallest body of the thread's life.

A compacted or summarised turn does not extend any stored window, so it mints a
new anchor -- which is correct, because the upstream prefix cache is genuinely
cold after compaction. There is no fuzzy, suffix-only, or "longest common
prefix" fallback: partial evidence merges unrelated threads onto one account,
which is strictly worse than the churn this module removes.

Item digests are domain-separated by (api key id, model class, **full**
instructions). Two threads whose instructions differ only after the first 512
characters -- which collide today -- therefore never share an anchor.

Large items
-----------
Two ceilings bound the work, and neither may collapse the window, because a
window that differs every turn mints a key every turn -- the exact churn this
module removes, and worst for the p90 453k-token cohort it exists to serve:

* ``_MAX_WINDOW_ENCODED_CHARS`` stops the backward walk, but never before
  ``_MIN_WINDOW_ITEMS`` items are recorded. A single trailing item larger than
  the whole-window ceiling therefore still yields a window deep enough to
  survive an ordinary append.
* an item whose canonical encoding exceeds ``_MAX_ITEM_ENCODED_CHARS`` is a
  **window boundary**: the walk stops there and the window is the items that
  follow it. It is not digested (a truncated digest would re-introduce the
  prefix collision this module removes) and it does not make the body
  unanchorable -- one 1 MiB tool result used to keep the whole thread
  unanchorable for as many turns as the window is deep. Two threads that differ
  only inside such an item and agree on the four items after it can merge; that
  requires four byte-identical items after the divergence and is accepted as
  the price of not stranding the thread.

Memory bound
------------
All state is process-local and positive-only, mirroring the repository's
existing bounded TTL-LRU process cache shape:

* at most ``_MAX_ANCHORS`` (2048) anchors, each holding at most
  ``_MAX_WINDOW_ITEMS`` (32) x ``_ITEM_DIGEST_BYTES`` (8) = 256 bytes of
  digests plus its key string;
* at most ``_MAX_INDEX_DIGESTS`` (65536) reverse-index rows, each holding at
  most ``_MAX_CANDIDATES_PER_DIGEST`` (8) thread-key references.

Both are LRU-evicted ``OrderedDict``s, so the worst case is **~17.6 MiB** per
replica -- measured with every cap saturated, counting the ``OrderedDict``
tables, the key strings and the per-row candidate lists, not just the digest
payload -- and never grows with traffic. Anchors also expire after the caller's
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
from enum import Enum
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
_MIN_ANCHORABLE_ITEMS = 1
# Floor the whole-window byte ceiling may not undercut. A window shallower than
# `_MIN_OVERLAP_ITEMS + items appended per turn` cannot survive an append, so a
# byte ceiling that stops the walk early turns the window into "the newest
# item", which differs every turn and mints a key every turn. Eight leaves room
# for a four-item turn (reasoning + call + output + message) on top of the
# four-item acceptance floor. Worst-case encoding work per request is therefore
# `_MIN_WINDOW_ITEMS * _MAX_ITEM_ENCODED_CHARS`, i.e. 8 MiB -- reachable only by
# a body that is itself at least 8 MiB, which the proxy has already parsed.
_MIN_WINDOW_ITEMS = 8
# Least evidence accepted for any reuse other than a byte-equal re-derivation.
# See "Matching contract": one or two shared leading/trailing items are
# routinely generic across independent threads, so accepting them merges those
# threads onto one key and one account and destroys the loser's window.
_MIN_OVERLAP_ITEMS = 4
# Serialization ceilings, applied *during* encoding so a 450k-token item is
# never materialised. Items are hashed in full -- a truncated digest would
# re-introduce exactly the prefix collision this module removes -- so an item
# past the per-item ceiling ends the window instead of being digested.
_MAX_ITEM_ENCODED_CHARS = 1024 * 1024
_MAX_WINDOW_ENCODED_CHARS = 256 * 1024
# LRU caps. See "Memory bound" above. Measured at ~17.6 MiB with every cap
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

# Deterministic per-item encoding: sorted keys, no insignificant whitespace,
# applied one item at a time so the window is bounded without materialising the
# whole list. These digests are compared only against each other inside this
# process, so the encoding has to be stable, not identical to any other
# fingerprint in the codebase.
#
# ``ensure_ascii`` is deliberately **off**. With it on, every non-ASCII code
# point becomes ``\uXXXX``: a Korean or Japanese transcript expands about
# sixfold, which both materialises megabytes for an item the walk may be about
# to refuse and trips ``_MAX_ITEM_ENCODED_CHARS`` at roughly a sixth of the
# documented size. Off, the encoded length tracks the raw length closely, which
# is also what makes the pre-check in ``_exceeds_item_ceiling`` tight. A str
# holding an unpaired surrogate then fails to encode and is reported
# ``UNENCODABLE`` (``UnicodeEncodeError`` is a ``ValueError``), which is the
# conservative answer for a body that is not representable anyway.
_ITEM_ENCODER = json.JSONEncoder(ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True, slots=True)
class ThreadWindow:
    """Chronological, bounded digest window over the tail of one input list."""

    digests: bytes
    item_count: int


@dataclass(slots=True)
class _Anchor:
    digests: bytes
    stored_at: float


def thread_anchor_domain(*, owner_row_id: str, model_class: str, instructions: str) -> bytes:
    """Domain separator mixed into every item digest.

    Length framing keeps distinct tuples distinct even when a component
    contains the separator. ``instructions`` is hashed in full, not truncated:
    two Codex system prompts that diverge only past 512 characters are
    different threads.
    """

    parts = (owner_row_id.encode(), model_class.encode(), instructions.encode())
    framed = b"".join(len(part).to_bytes(8, "big") + part for part in parts)
    # ``owner_row_id`` is the API key's row id, not the key or any secret
    # material, and this digest is never stored or compared as a credential: it
    # is a process-local domain separator that keeps one tenant's thread anchors
    # from colliding with another's. A deliberately slow password hash would run
    # on every turn for no security benefit.
    return sha256(framed).digest()  # codeql[py/weak-sensitive-data-hashing]


class _ItemVerdict(Enum):
    """Why an item was not digested. Both refuse a truncated digest."""

    # Encoding exceeds ``_MAX_ITEM_ENCODED_CHARS``. The walk stops here and
    # keeps the items that follow: the alternative -- reporting the whole body
    # unanchorable -- strands the thread for as many turns as the window is
    # deep while the oversized item stays in reach of the backward walk.
    OVERSIZED = "oversized"
    # The canonical encoder cannot represent the item, so no window containing
    # or bounded by it is reproducible. The body is unanchorable.
    UNENCODABLE = "unencodable"


def _exceeds_item_ceiling(item: JsonValue) -> bool:
    """Cheap structural pre-check for the per-item ceiling.

    ``iterencode`` streams a container, but it yields a single *scalar* -- a
    450k-token text part is one string -- as one already-materialised chunk, so
    the size check inside the encode loop can only fire after allocating it.
    The sum of raw string lengths is a lower bound on the canonical encoding
    length (escaping and punctuation only add), so exceeding the ceiling here
    proves the encoding would exceed it, and the walk never materialises the
    encoding of an item it is going to refuse. Staying under is not a proof, so
    the encode loop keeps its own exact check -- but with ``ensure_ascii`` off
    the two are close, rather than a factor of six apart on non-ASCII text.
    """

    pending: list[JsonValue] = [item]
    total = 0
    while pending:
        node = pending.pop()
        if isinstance(node, str):
            total += len(node)
        elif isinstance(node, dict):
            pending.extend(node.keys())
            pending.extend(node.values())
        elif isinstance(node, list):
            pending.extend(node)
        else:
            # Numbers, booleans and null all encode to a handful of characters.
            total += 1
        if total > _MAX_ITEM_ENCODED_CHARS:
            return True
    return False


def _bounded_item_digest(domain: bytes, item: JsonValue) -> tuple[bytes, int] | _ItemVerdict:
    """Digest one item's full canonical JSON, or say why it was not digested.

    ``iterencode`` yields chunks lazily and each chunk is folded straight into
    the hash, so the encoding is never materialised.
    """

    if _exceeds_item_ceiling(item):
        return _ItemVerdict.OVERSIZED
    hasher = sha256(domain + b"\x1e")
    size = 0
    try:
        for chunk in _ITEM_ENCODER.iterencode(item):
            size += len(chunk)
            if size > _MAX_ITEM_ENCODED_CHARS:
                return _ItemVerdict.OVERSIZED
            hasher.update(chunk.encode())
    except (TypeError, ValueError):
        return _ItemVerdict.UNENCODABLE
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
    if len(items) < _MIN_ANCHORABLE_ITEMS:
        return None
    digests: list[bytes] = []
    encoded_chars = 0
    for item in reversed(items):
        if len(digests) >= _MAX_WINDOW_ITEMS:
            break
        # The byte ceiling may only stop a walk that already holds a window
        # deep enough to survive an append. Stopping earlier records "the
        # newest item", which differs every turn.
        if encoded_chars >= _MAX_WINDOW_ENCODED_CHARS and len(digests) >= _MIN_WINDOW_ITEMS:
            break
        digested = _bounded_item_digest(domain, item)
        if digested is _ItemVerdict.UNENCODABLE:
            return None
        if digested is _ItemVerdict.OVERSIZED:
            # Window boundary, not poison: keep the items already collected.
            break
        assert not isinstance(digested, _ItemVerdict)
        digest, encoded_size = digested
        encoded_chars += encoded_size
        digests.append(digest)
    if len(digests) < _MIN_ANCHORABLE_ITEMS:
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


def _accepts_overlap(stored: bytes, incoming: bytes, overlap_items: int) -> bool:
    """Whether ``overlap_items`` matched items may reuse ``stored``'s key.

    The rule, and nothing else:

    1. at least ``_MIN_OVERLAP_ITEMS`` items matched, or
    2. the two windows are byte-equal -- the same items in the same order, an
       identical re-derivation of one body, where nothing is being extended and
       so there is no shared-opening ambiguity to resolve.

    Rule 1 is deliberately applied to a stored window that the incoming window
    merely *extends* as well. "Thread B opens with everything thread A has
    recorded" and "thread A appended a turn" produce byte-identical evidence,
    so the only available defence is to demand a shared run too long to be a
    generic opening. Accepting less hands A's key to B and then lets B's
    ``register`` overwrite A's window, so A permanently loses the key it owns:
    a false merge pins unrelated work to one account and is worse than the
    churn this module removes.
    """

    if overlap_items <= 0:
        return False
    if overlap_items >= _MIN_OVERLAP_ITEMS:
        return True
    return stored == incoming


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
                    dropped = self._anchors.pop(thread_key, None)
                    if dropped is not None:
                        self._forget_index_references(thread_key, dropped.digests)
                    elif thread_key in candidates:
                        candidates.remove(thread_key)
                    continue
                if thread_key in seen:
                    continue
                seen.add(thread_key)
                overlap = _overlap_items(anchor.digests, window.digests)
                accepted = _accepts_overlap(anchor.digests, window.digests, overlap)
                if accepted and overlap > best_overlap:
                    best_key = thread_key
                    best_overlap = overlap
            if not candidates:
                self._index.pop(digest, None)
        if best_key is not None:
            self._anchors.move_to_end(best_key)
        return best_key

    def _forget_index_references(self, thread_key: str, digests: bytes) -> None:
        """Remove one thread key from every digest row its window indexed.

        Called whenever an anchor stops being reachable under a window --
        expiry, LRU eviction, or a re-register that replaces the window. Left
        behind, a dead key keeps occupying one of the
        ``_max_candidates_per_digest`` slots on a popular digest row and can
        crowd a live anchor out of a row before that anchor is ever verified.
        """

        for offset in range(0, len(digests), _ITEM_DIGEST_BYTES):
            digest = digests[offset : offset + _ITEM_DIGEST_BYTES]
            row = self._index.get(digest)
            if row is None:
                continue
            if thread_key in row:
                row.remove(thread_key)
            if not row:
                self._index.pop(digest, None)

    def register(self, thread_key: str, window: ThreadWindow) -> None:
        """Record this turn's window as the thread's anchor and index it."""

        previous = self._anchors.get(thread_key)
        if previous is not None and previous.digests != window.digests:
            # The old window's digests no longer reach this thread.
            self._forget_index_references(thread_key, previous.digests)
        self._anchors[thread_key] = _Anchor(digests=window.digests, stored_at=self._clock.monotonic())
        self._anchors.move_to_end(thread_key)
        while len(self._anchors) > self._max_anchors:
            evicted_key, evicted = self._anchors.popitem(last=False)
            self._forget_index_references(evicted_key, evicted.digests)
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
