# Context: allow-bounded-inline-images-on-bridge

## Purpose

Let bounded inline JPEG/PNG images keep their HTTP-responses-bridge session
(connection + prompt-cache reuse) by default, make every oversize outcome an
explicit client error, and stop treating upstream close 1009 as transport
failure. Full rationale for the budgets and the default-on decision lives
here; the requirement text stays normative.

## Evidence and budget arithmetic

- Operator observations: ~4.9 MB decoded JPEG (~6.55 MB frame) succeeds on
  the bridge; ~12.5 MB decoded JPEG (~16.69 MB frame) dies with upstream
  websocket close 1009 before any event. The upstream's true ceiling is
  unknown; the local 64 MiB frame budget is NOT an upstream acceptance
  guarantee, and the 1009 classification reports upstream rejection honestly
  when it happens.
- Per-image budget: DECODED bytes, inclusive 5,000,000. Encoding
  5,000,000 bytes takes at most ceil(5,000,000/3)*4 = 6,666,668 base64
  characters, so a longer segment always decodes over budget and is rejected
  without decoding (the multi-MiB decode is the expensive step). At or below
  that bound a strict `b64decode(validate=True)` runs anyway and its exact
  length decides; padding means a 6,666,668-character segment decodes to at
  most 5,000,001 bytes, which the exact check rejects.
- Frame budget: 64 MiB (67,108,864 bytes) on the COMPLETE serialized
  `response.create` frame — envelope, operation id, thread-cache identity and
  installation metadata included — measured exactly at the final send (an
  earlier serialized-size estimate rejects clear oversize sooner). The
  owner explicitly retained this total so several legal images plus history
  are not blocked by a new local cap; upstream may still reject them.

## Decision log

- **Why default-on:** the bypass is the anomaly, not the behavior. Native
  websocket clients already carry inline `data:` images; the bridge bypass
  exists to free pending slots, and it costs every image thread its cache
  locality. A default-off contract would leave the observed regression in
  place for every stock deployment.
- **Why a kill switch anyway:** this changes routing on a hot proxy path.
  `CODEX_LB_HTTP_RESPONSES_SESSION_BRIDGE_INLINE_IMAGES_ENABLED=false` is
  the documented no-deploy rollback to the exact pre-change bypass. It is
  T4 (incident lever), not a tunable, so it has no dashboard column.
- **Why decoded bytes:** the operator reason about (and the upstream
  re-encodes) decoded image size, not base64 wire length.
- **Why explicit 400 beats silent bypass for oversize:** the raw path would
  accept the bytes locally (128 MiB ingress) and fail late, or reroute the
  thread mid-conversation losing cache locality without telling anyone. The
  pre-existing anti-retry 400 `payload_too_large` envelope is the
  established contract.
- **Why unsupported shapes keep the bypass:** external URLs genuinely need
  the raw path; malformed parts are upstream validation questions. Splitting
  the shape verdict from the size verdict is what makes "size never selects
  the bypass" enforceable.
- **Why close 1009 is classified at the reader/relay layer:** all adapters
  surface the peer close code on the shared message object, and the terminal
  surfacing path is shared, so one exact-code classifier covers aiohttp,
  `websockets` and the native helper. A locally generated 1009 (an adapter's
  own cap rejecting an inbound message) is deliberately classified the same:
  both mean "a websocket message exceeded a message-size limit".
- **Mixed shapes:** any unsupported shape anywhere keeps the blanket bypass
  even when a sibling image is over budget (unsupported wins); an all-inline
  request with any over-budget image is the explicit 400.

## Failure modes

- Image > 5,000,000 decoded bytes: pre-send 400 `payload_too_large`
  (`param=input`, `invalid_request_error`); lane and reservation settle; the
  next request proceeds.
- Final frame > 64 MiB: same 400, raised at the early estimate and again at
  the authoritative final measurement (catches metadata growth).
- Upstream close 1009: 400 when uncommitted, SSE `response.failed` envelope
  when committed; no retry, no exclusion/rotation, no error-health write.
- Rollback `false`: every image request takes the blanket bypass again.

## Example

A 4,999,999-byte PNG rides the bridge verbatim on the thread's existing
connection. A 5,000,001-byte PNG gets 400 `payload_too_large` before any
upstream send. Five 4.9 MB images plus long history (frame > 16 MiB but
< 64 MiB) are locally admitted; if the upstream then closes 1009, the client
sees the terminal 400/SSE `payload_too_large`, the account stays healthy,
and the next turn on the thread proceeds.
