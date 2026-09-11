# Change: anchor-unanchored-http-thread-cache-keys

## Why

Responses requests that arrive with no continuity identifier at all — no
`conversation`, no `previous_response_id`, no Codex session/thread header, no
client `prompt_cache_key` — are supposed to be held on one account by the
`prompt_cache_key` the proxy derives for them. On this fleet (28 pooled
accounts) they are not. On a day with **zero** overload isolation events,
unanchored turns switched account **60%** of the time between consecutive
turns and cached **51.2%** of their input tokens, while turns carrying
`conversation_id` switched **8%** and cached **91.0%**. Unanchored traffic is
~28% of all proxy traffic (16,644 requests in a 10-hour window, median
`input_tokens` 49,810, p90 453,374); on the clean day it burned 1.52 B input
tokens and cached 0.78 B, so roughly **0.6 B input tokens/day** are resent for
nothing. Per-client cache-hit ratio inside that traffic ranges from 1% to 87%,
which is the signature of a derivation that depends on payload shape rather
than on thread identity.

`_derive_prompt_cache_key` hashes model class, `api_key.id[:12]`,
`instructions[:512]`, the system/developer input text `[:512]` and the *first
user item* `[:512]`. Append-only histories do stay stable, which is why one
`fasthttp` client reaches 87%. Everything else breaks:

- any client that trims or compacts history, or writes a timestamp/cwd into
  its first user item, changes the key **every turn** and is treated as a new
  thread each time;
- two independent threads that share their first 512 characters — for
  `codex_cli_rs`, every session whose first item is the same
  `<environment_context>` block for the same cwd — collapse onto **one** key,
  which pins unrelated work to one account;
- two threads whose `instructions` differ only after 512 characters collide
  the same way;
- a turn whose input has no `role == "user"` item degenerates to one global
  bucket per API key;
- with `api_key_auth_enabled=false` the empty-ingredient branch returns
  `uuid4().hex[:24]`, a sticky key that can never be hit again yet still
  writes a `sticky_sessions` row;
- `_extract_first_user_input` joins every text part of the first user item
  before slicing to 512 characters, so a p90 453k-token payload is
  concatenated on the hot path to produce 512 bytes.

## What Changes

- New `app/modules/proxy/thread_anchors.py`: a process-local, TTL- and
  LRU-bounded index from a turn's item-digest window to the thread key already
  minted for that thread, modelled on `model_source_pins.PinCache`.
- `_derive_prompt_cache_key` now anchors instead of hashing text. A turn reuses
  a thread's key **only** when the recorded item sequence, from some offset to
  its end, is exactly the head of the new turn's items, with at least two items
  matched. That covers appends, a client trimming its leading history, and an
  identical re-derivation of the same body (bridge-to-HTTP fallback,
  cross-transport replay). Nothing fuzzy, truncated, or summary-aware matches,
  so this is **strictly fewer** false merges than today, not more: the two
  512-character collision classes above disappear because item digests are
  domain-separated by API key, model class and the **complete** `instructions`.
- Compaction is handled by admitting it, not by bridging it: a compacted turn
  does not extend the recorded transcript, mints a new key, and is reported as
  `anchor_reset`. The upstream prefix cache is genuinely cold there.
- The `uuid4` branch is gone. A body with nothing to anchor (a non-list input,
  an empty input list, or an item too large to digest exactly) still gets a
  stable derived `prompt_cache_key` forwarded upstream — the spec's MUST — but
  supplies **no** sticky routing key, so selection takes the unbound path and
  no single-use `sticky_sessions` row is written.
- Serialization is bounded *during* encoding (`iterencode`, chunks folded
  straight into the hash, 256 KiB per window, 32 items), so the unbounded join
  on the hot path is gone. Items are hashed in **full**: a truncated digest
  would re-introduce the prefix collision being removed, so an item past the
  1 MiB per-item ceiling makes the body unanchorable instead.
- Diagnostics: every resolution reports `payload` / `disabled` / `anchor_hit` /
  `anchor_new` / `anchor_reset` / `unanchorable` on the existing
  `proxy_request_shape` trace line next to `sticky_key_source`, and on a new
  `codex_lb_prompt_cache_key_derivation_total{outcome}` counter. This answers
  the open objection on #2347 — that the production percentages were
  reconstructed rather than correlated to resolved keys — from the fleet
  itself.
- Migration: minted keys carry a `v2t-` prefix (`v2u-` for unanchorable), so
  they are distinguishable from the retired `{model_class}-{api_key}-{hash}`
  shape. `prompt_cache` rows of the old shape already expire through
  `purge_prompt_cache_before`; `sticky_thread` rows have no TTL, so the cleanup
  scheduler runs a one-shot bounded sweep over the three retired prefixes and
  retires itself once a pass finds nothing.

No new setting. The behaviour rides the existing dashboard
`openai_cache_affinity` boolean, as the audit recommends, so the settings
ratchet is unchanged and there is no new `CODEX_LB_*` env var, dashboard column
or migration.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: MODIFIED requirement "Codex backend session_id
  preserves account affinity" (the derived-key sentence now points at the
  anchored derivation and admits the unanchorable outcome; one scenario
  added). ADDED requirement "Unanchored Responses turns are anchored to a
  verified thread key".

## Impact

- Code: `app/modules/proxy/thread_anchors.py` (new),
  `app/modules/proxy/affinity.py` (derivation, resolution result, one policy
  field; the three `_extract_*` text helpers are deleted),
  `app/modules/proxy/_service/compact.py`,
  `app/modules/proxy/_service/observability.py`,
  `app/modules/proxy/_service/streaming/retry.py` (one diagnostic argument;
  derivation still runs *after* the transport decision, so no request moves
  between HTTP and WebSocket), `app/core/metrics/prometheus.py`,
  `app/modules/sticky_sessions/cleanup_scheduler.py`.
- `http_continuation.inferred_http_bridge_key` has the identical defect and is
  deliberately **not** changed here: different blast radius (bridge socket
  sharing), and this derivation should be measurable on its own first.
- Data: no schema change. Row volume moves from a few coarse keys to one row
  per live thread; `prompt_cache` rows expire as before, the new recurring
  purge gives the `sticky_thread` kind the same bound, and the one-shot sweep
  removes the retired shape's leftovers.
- Operators: no action. Anchors are per process, so a restart or a blue/green
  window where both colors serve can leave one thread holding two keys and two
  owners for that window — bounded by the freshness window and worth one
  prefix-cache miss. Watch `codex_lb_prompt_cache_key_derivation_total` after
  deploy: a healthy fleet shows `anchor_hit` dominating `anchor_new` +
  `anchor_reset` for unanchored traffic.

## Honest limits

This recovers locality for threads that resend a transcript and either append
to it or trim its front. It does **not** recover: a client that compacts every
turn (each compaction is a genuine cache reset), a delta-only turn that shares
no item with the previous one, a body carrying an item too large to digest
exactly, an empty body, or anything after a process restart or an anchor
eviction. Those cases are now *reported* — `anchor_reset` or `unanchorable` —
rather than silently served by a key that looks sticky and is not. It also does
nothing for `inferred_http_bridge_key`, which has the same defect and is left
for a follow-up so this change can be measured on its own.
