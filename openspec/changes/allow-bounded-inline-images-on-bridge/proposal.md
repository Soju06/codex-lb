# Change: allow-bounded-inline-images-on-bridge

## Why

Every `/v1/responses` request carrying an `input_image` part bypasses the
HTTP responses bridge today, so each image-bearing turn loses the bridge
connection and its prompt-cache reuse for the whole thread. Meanwhile an
inline image that is too large for the upstream websocket surfaces as a
retryable `stream_incomplete` with account exclusion (the observed close
1009 masking, #2465 Blocker 2).

Two operator-observed facts bound the fix: a ~4.9 MB decoded JPEG
(~6.55 MB serialized frame) rides the bridge fine, while a ~12.5 MB decoded
JPEG (~16.69 MB frame) is rejected by the upstream websocket with close code
1009 before any event. The exact upstream ceiling is unknown and not claimed
here; the contracts below are local engineering policy under that evidence.

## What Changes

- **Default-on bounded admission.** A request whose every `input_image`
  part is an inline `data:image/(jpeg|png);base64,` URL decoding to at most
  5,000,000 bytes (inclusive) keeps using the bridge, guarded by one new
  T4 kill switch, `http_responses_session_bridge_inline_images_enabled`
  (default `true`). Explicit `false` restores today's blanket image bypass.
- **Explicit oversize errors, never silent fallback.** A shape-valid image
  decoding above 5,000,000 bytes — or an admitted request whose complete
  serialized `response.create` frame (metadata included) exceeds 64 MiB —
  fails with the pre-send 400 `payload_too_large` client error. Size alone
  never selects the raw-HTTP bypass and historical slimming never runs for
  image-bearing bridge requests.
- **Fail-closed shapes.** External URLs, other media types/schemes,
  malformed base64, `file_id`/`sediment` references and `image_generation`
  requests keep today's bypass/reject behavior; any unsupported shape
  anywhere wins over an over-budget sibling.
- **Close 1009 is terminal payload evidence.** An upstream websocket close
  1009 is classified as the same non-retryable 400 `payload_too_large`
  client error (SSE `response.failed` envelope once events have streamed),
  with no account penalty, exclusion or rotation; 1006/None and 1000 keep
  their existing semantics.
- **Transport plumbing for the 64 MiB envelope.** Bridge upstream sockets
  get per-connection 64 MiB receive caps on every adapter, and the native
  helper's oversized websocket-text IPC is carried as chunked
  `websocket_text` events under the required `websocket_text_chunking_v1`
  pair capability, with the native frame/message caps coupled.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: adds the bounded inline-image bridge contract as
  an explicit exception to the blanket image bypass, and the close-1009
  terminal classification.

## Impact

- Text-only behavior is unchanged everywhere (same budgets, same slimming).
- One new setting (settings surface 97, simplicity budget fenced to match);
  no dashboard column; docs go to `docs/routing.md` and this change only.
- Python and the native egress helper must deploy as a pair: the parent
  refuses a helper without `websocket_text_chunking_v1` at the handshake.
- Refs #2465 (partial: Blocker 2), #2409, #2425, #2455.
