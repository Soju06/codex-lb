# Tasks

## 1. Anchor index

- [x] 1.1 Add `app/modules/proxy/thread_anchors.py` with a bounded item-digest
  window builder (`iterencode`-bounded, 16 KiB/item, 256 KiB/window, 32 items)
  and a `ThreadAnchorIndex` with LRU caps, an injected `Clock`, and a TTL.
- [x] 1.2 Domain-separate item digests by API key, model class and the complete
  `instructions`.
- [x] 1.3 Verify every candidate with an exact item-digest comparison; accept
  only an exact extension of the recorded window with >= 2 items matched.

## 2. Derivation

- [x] 2.1 Replace `_derive_prompt_cache_key` with the anchored derivation and
  delete the `uuid4` fallback and the three unbounded `_extract_*` helpers.
- [x] 2.2 Return a resolution carrying the attached key, the sticky key
  (`None` when unanchorable) and the outcome; withhold the sticky key without
  withholding the forwarded `prompt_cache_key`.
- [x] 2.3 Thread the dashboard freshness window into the derivation as the
  anchor TTL from both the responses and compact affinity helpers.

## 3. Diagnostics

- [x] 3.1 Add `codex_lb_prompt_cache_key_derivation_total{outcome}`.
- [x] 3.2 Carry the outcome on `_AffinityPolicy` and log it on
  `proxy_request_shape` next to `sticky_key_source` for both stream and
  compact.

## 4. Migration

- [x] 4.1 Version-prefix minted keys (`v2t-` / `v2u-`).
- [x] 4.2 One-shot bounded sweep of retired-shape `sticky_thread` rows in the
  sticky-session cleanup pass, self-retiring once a pass finds nothing.
- [x] 4.3 Recurring bounded purge of anchored (`v2t-`) `sticky_thread` rows idle
  past the freshness window, so the no-TTL kind stays bounded.

## 5. Tests

- [x] 5.1 Stability across a growing transcript, including past the retained
  window, and under a randomized append sequence.
- [x] 5.2 Stability when the client trims leading history.
- [x] 5.3 Distinct threads from one API key stay distinct, including the two
  512-character collision classes.
- [x] 5.4 Memory bound: anchor and index caps hold; LRU and TTL eviction.
- [x] 5.5 Unanchorable bodies report the outcome, attach a stable key, and
  supply no sticky key.
- [x] 5.6 Compaction mints a new anchor and reports `anchor_reset`.
- [x] 5.7 Cleanup scheduler sweeps retired-shape `sticky_thread` rows once and
  keeps purging anchored ones.

## 6. Validation

- [x] 6.1 `openspec validate anchor-unanchored-http-thread-cache-keys --strict`
- [x] 6.2 `ruff check` / `ruff format --check`, affinity/sticky/selection unit
  suites, `codex review --base origin/main`.
