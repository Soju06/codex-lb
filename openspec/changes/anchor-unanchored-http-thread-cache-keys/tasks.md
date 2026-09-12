# Tasks

## 1. Anchor index

- [x] 1.1 Add `app/modules/proxy/thread_anchors.py` with a bounded item-digest
  window builder (`iterencode`-bounded, 16 KiB/item, 256 KiB/window, 32 items)
  and a `ThreadAnchorIndex` with LRU caps, an injected `Clock`, and a TTL.
- [x] 1.2 Domain-separate item digests by API key, model class and the complete
  `instructions`.
- [x] 1.3 Verify every candidate with an exact item-digest comparison; accept
  only an exact extension of the recorded window with >= 4 items matched, or a
  byte-equal window (re-derivation of the same body). A shorter recorded window
  that the new turn merely extends is refused: that is the shared-preamble
  false merge, which also destroys the loser's window.
- [x] 1.4 Floor the window at `_MIN_WINDOW_ITEMS` (8) items so the
  whole-window byte ceiling cannot collapse it to "the newest item", and make
  an item past the per-item ceiling a window boundary instead of poison.
- [x] 1.5 Drop reverse-index candidate references when an anchor expires, is
  LRU-evicted, or has its window replaced, so a dead key cannot occupy a
  capped candidate slot on a shared digest.
- [x] 1.6 Enforce the per-item ceiling with a structural pre-check, because
  `iterencode` yields a large string scalar as one already-materialised chunk.
- [x] 1.7 Encode items with `ensure_ascii=False`. With it on, non-Latin text
  expands ~6x, which both materialises megabytes for an item about to be
  refused and trips the per-item ceiling at a sixth of its documented size.

## 2. Derivation

- [x] 2.1 Replace `_derive_prompt_cache_key` with the anchored derivation and
  delete the `uuid4` fallback and the three unbounded `_extract_*` helpers.
- [x] 2.2 Return a resolution carrying one key (`None` when unanchorable) and
  the outcome. When unanchorable, attach nothing to the payload: a written
  placeholder is read back as client-supplied on the second resolution of the
  same payload object (bridge -> `_stream_with_retry` fallback) and becomes a
  constant per-API-key sticky key.
- [x] 2.3 Thread the dashboard freshness window into the derivation as the
  anchor TTL from both the responses and compact affinity helpers.

## 3. Diagnostics

- [x] 3.1 Add `codex_lb_prompt_cache_key_derivation_total{outcome}`.
- [x] 3.2 Carry the outcome on `_AffinityPolicy` and log it on
  `proxy_request_shape` next to `sticky_key_source` for both stream and
  compact.

## 4. Migration

- [x] 4.1 Version-prefix minted keys (`v2t-`).
- [x] 4.2 No `sticky_thread` key-prefix sweep: a derived key is only ever a
  `prompt_cache` row (the `sticky_thread` branch is reachable only with cache
  affinity off, where the derivation supplies no key), and
  `purge_prompt_cache_before` already retires both shapes. A `sticky_thread`
  row of a derived-looking shape is client-supplied and must survive.

## 5. Tests

- [x] 5.1 Stability across a growing transcript, including past the retained
  window, and under a randomized append sequence.
- [x] 5.2 Stability when the client trims leading history.
- [x] 5.3 Distinct threads from one API key stay distinct, including the two
  512-character collision classes.
- [x] 5.4 Memory bound: anchor and index caps hold; LRU and TTL eviction.
- [x] 5.5 Unanchorable bodies report the outcome, attach nothing, and supply no
  sticky key on a first *and* a second resolution of the same payload object.
- [x] 5.6 Compaction mints a new anchor and reports `anchor_reset`.
- [x] 5.7 Cleanup pass never purges `sticky_thread` by key prefix, proven
  against the real database: a client-supplied `codex-`/`std-`/`v2t-`
  `sticky_thread` row survives while derived `prompt_cache` rows expire.
- [x] 5.8 Multi-turn stability at 96 KiB and 256 KiB per item (window floor).
- [x] 5.9 One oversized item bounds the window and the thread re-anchors.
- [x] 5.10 A shared one- or two-item opening never transfers a key, and the
  thread that owns a key never loses it to such a claim.
- [x] 5.11 Route-level `/v1/responses` coverage for the overlap floor: the
  early reset on a one-item opening, then the account held once the transcript
  clears the floor even after usage flips.
- [x] 5.12 Eviction and re-register leave no reverse-index references, and an
  oversized item is never handed to the encoder.
- [x] 5.13 A large non-ASCII item is digested, not refused, and its encoded
  size tracks its raw size.

## 6. Validation

- [x] 6.1 `openspec validate anchor-unanchored-http-thread-cache-keys --strict`
- [x] 6.2 `ruff check` / `ruff format --check`, affinity/sticky/selection unit
  suites, `codex review --base origin/main`.
