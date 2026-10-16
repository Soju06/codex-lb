# Tasks: allow-bounded-inline-images-on-bridge

## 1. Implementation

- [x] 1.1 `app/core/config/settings.py` + `app/core/config/tiers.py`: add
  `http_responses_session_bridge_inline_images_enabled: bool = True` (T4),
  documented as the rollback kill switch; regenerate
  `docs/reference/settings.md` and fence the settings budget 96 -> 97.
- [x] 1.2 `app/modules/proxy/_service/http_bridge/helpers.py`: admission
  constants and verdicts (per-image decoded budget with the 6,666,668-char
  fast path, recursive fail-closed walk, runtime-config flag), the 64 MiB
  frame budget accessor, and `_open_http_bridge_upstream_with_budget`.
- [x] 1.3 `app/modules/proxy/_service/http_bridge/streaming.py`: routing gate
  — compute admission before the payload-size bypass, exempt admitted
  requests from the size bypass and the size leg of the transport pin,
  explicit per-image/frame 400s after the operator pin and health fallback,
  and thread the whole-frame override into the bridge submit path.
- [x] 1.4 `app/modules/proxy/_service/support.py`,
  `request_submit.py`, `response_create.py`: `response_create_max_bytes`
  override plumbing (prepare -> state -> final send) with slimming disabled
  for override-carrying requests.
- [x] 1.5 Close-1009 classification: `app/core/clients/proxy_websocket.py`
  constant + predicate, `streaming/helpers.py` classifier literal,
  `websocket/helpers.py` terminal message, `websocket/mixin.py` relay
  handling (no replay, no penalty, 400 normalization) and
  `http_bridge/upstream_events.py` reader handling, plus the
  `service.py`/stub re-exports.
- [x] 1.6 64 MiB receive envelope: `max_message_bytes` through
  `connect_responses_websocket`/`_connect_upstream_websocket` into every
  adapter, and `_open_upstream_websocket_with_budget` on the service.
- [x] 1.7 Native pair: `websocket_text_chunking_v1` capability (protocol +
  handshake fixture + required set), Python pump reassembly with fail-closed
  interruption, Rust chunked emission, and the coupled
  `max_frame_size`/`max_message_size`.
- [x] 1.8 `docs/routing.md`: document the default-on contract, budgets,
  rollback and 1009 classification; no README/CHANGELOG changes.

## 2. Regression coverage (externally failing route)

- [x] 2.1 Route: text -> image -> text reuses one bridge connection with
  verbatim image bytes and `prompt_cache_key` continuity.
- [x] 2.2 Route: ~4.9 MB image rides the bridge; 5,000,001 decoded bytes is
  the pre-send 400 `payload_too_large` with the lane settled and the next
  request healthy; shrunk frame cap rejects multiple legal images.
- [x] 2.3 Route: five legal images + long history (frame > 16 MiB) admitted.
- [x] 2.4 Route: close 1009 before events (terminal 400, one dispatch,
  follow-up healthy) and after events (SSE `response.failed`, no replay);
  generic-disconnect control unchanged.
- [x] 2.5 Route: upstream image rejection before `response.created` (error
  and `response.failed` frames) surfaces 400 and releases the bridge slot.
- [x] 2.6 Route: genuinely silent upstream under a short eventless deadline
  recovers bounded (and client cancellation settles the lane).
- [x] 2.7 Unit: verdict table (shapes, boundaries, fast path), precedence,
  sibling-field walker regression, env parsing, runtime-config mapping,
  routing gate; cap plumbing per adapter; chunk reassembly fail-closed;
  1009 classifier exactness; native IPC pair with the real helper binary.

## 3. Verification

- [x] 3.1 Prove the route regressions fail on the stock tree (reverse-apply
  app diff only) and pass with it restored.
- [x] 3.2 `ruff format`/`ruff check`/`ty` on touched app+tests; settings
  reference + tier scripts; architecture/cancellation/timing checks;
  `openspec validate --strict` for the change and `--specs` for main.
- [x] 3.3 Rust: fmt, clippy, `cargo test` for the touched crates (isolated
  container, no host cargo); native IPC pair probe with the built helper.
