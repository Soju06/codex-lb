# Change: allow-validated-inline-images-on-http-bridge

## Why

The HTTP responses bridge (WebSocket-backed) provides prompt-cache reuse,
`previous_response_id` session continuity, and incremental input across
multi-turn tool loops. Since #903 every request containing an `input_image`
part bypasses the bridge and is forced onto the raw HTTP stream path, so a
single historical inline image (e.g. a screenshot read by the agent) drops a
long coding session onto HTTP for every later turn — losing the bridge's
cache/session affinity even though the image is a perfectly valid inline
`data:` URL.

Live validation on a real proxy (codex-lb 1.22.0) showed the cost: image-heavy
sessions over HTTP cached ~20-43% of input tokens while equivalent text-only
WebSocket traffic cached ~93%; the same request over the WebSocket bridge
cached 89% on the second call. OpenAI's Responses WebSocket accepts inline
`data:image/...` input images; the bridge even contains the inlining logic for
external image URLs (`_inline_http_bridge_image_urls`).

The reason #903 bypasses unconditionally is that a malformed image could
occupy a bridge pending slot until local timeouts instead of failing fast with
upstream validation errors. That concern only applies to images the proxy has
not validated.

## What Changes

- The image bridge bypass becomes conditional. A `/v1/responses` request with
  `input_image` parts MAY use the HTTP responses bridge when **all** of the
  following hold:
  - the full payload fits the WebSocket transport payload budget
    (`_ws_transport_payload_budget_bytes`);
  - every `input_image` part is an inline `data:image/<type>;base64,...` URL;
  - the base64 payload decodes;
  - the decoded bytes match the declared media type and carry parseable
    dimensions (PNG IHDR, GIF header, JPEG SOF, WebP VP8X/VP8/VP8L);
  - each dimension is at least `_INLINE_IMAGE_MIN_DIMENSION` (64px) and the
    total pixel count is at most `_INLINE_IMAGE_MAX_PIXELS` (100M), so
    pathological/degenerate images cannot reach the upstream WebSocket.
- If any check fails, the existing behavior is preserved: the bridge is
  bypassed and the request is forced onto the raw HTTP stream path with
  `upstream_stream_transport_override="http"`, keeping fast upstream
  validation errors for invalid image payloads.
- `image_generation` tool requests keep the existing unconditional bypass
  (they are not inline-image validation cases).
- Image requests that pass validation still exercise the bridge's existing
  request-local guards (size budget, admission, retry safety); they are not a
  new hole around `_raise_for_unsupported_input_image_references`, which still
  rejects `file_id`/`sediment://` references before this decision runs.

## Impact

- Valid inline images (the common case: agent screenshots, user attachments)
  regain HTTP bridge prompt-cache/session reuse and per-turn incremental
  continuity.
- Invalid, truncated, mismatched, or degenerate inline images still fail fast
  on the raw HTTP path instead of occupying bridge pending slots.
- No API, schema, or configuration surface changes; the validation is purely
  request-local and conservative (fail closed to HTTP on any doubt).

## Verification

- Unit: `_responses_request_has_only_valid_inline_images` accepts real
  PNG/WebP/JPEG inline images (nested content and tool output) and rejects
  tiny (1x1), non-base64, non-image, and external-URL inputs.
- Unit: `_stream_http_bridge_or_retry` uses the bridge for a valid 64x64
  inline PNG and bypasses to HTTP (override `"http"`) for a 1x1 PNG.
- Existing #903 regression tests still pass unchanged (truncated PNG fixtures
  remain on the HTTP path).
- Live (1.22.0 install, patched): valid inline PNG → `upstream_transport=websocket`,
  second identical request `cached_tokens` 7,936/8,916 (~89%); invalid base64
  image → HTTP 400 `invalid_value` in ~0.45s; 1x1 PNG → HTTP transport.
