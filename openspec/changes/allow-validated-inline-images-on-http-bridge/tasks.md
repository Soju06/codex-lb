# Tasks: allow-validated-inline-images-on-http-bridge

## 1. Implementation

- [x] 1.1 `app/modules/proxy/_service/http_bridge/streaming.py`: add
      `_responses_request_has_only_valid_inline_images` (inline `data:image/*;base64`
      check + media-type signature/dimension parsing for PNG/GIF/JPEG/WebP with
      min-dimension and max-pixel guards) and gate the image bypass on it:
      bridge allowed only when the payload fits the WS budget and every inline
      image validates; otherwise preserve the HTTP override.
- [x] 1.2 `app/modules/proxy/service.py`: re-export
      `_responses_request_has_only_valid_inline_images` for tests/observability.

## 2. Regression coverage

- [x] 2.1 Unit: `_responses_request_has_only_valid_inline_images` accepts
      real inline PNG (nested message content and tool output) and rejects
      text-only payloads.
- [x] 2.2 Unit: the same helper rejects tiny (1x1) images, invalid base64,
      non-image bytes, and external `https://` image URLs.
- [x] 2.3 Unit: `_stream_http_bridge_or_retry` routes a valid 64x64 inline PNG
      through the bridge (no upstream transport override).
- [x] 2.4 Unit: `_stream_http_bridge_or_retry` bypasses the bridge for a 1x1
      inline PNG with `upstream_stream_transport_override="http"`.
- [x] 2.5 Existing #903 tests (`test_stream_http_bridge_or_retry_bypasses_bridge_for_input_image`,
      `test_responses_request_contains_input_image_detects_supported_shapes`)
      pass unchanged with truncated-PNG fixtures still on the HTTP path.

## 3. Verification

- [x] 3.1 `uv run ruff check` on modified source and tests.
- [x] 3.2 `uv run pytest tests/unit/test_proxy_utils.py -q` (993 passed).
- [x] 3.3 `uv run ty check` on modified source and tests.
- [x] 3.4 Live validation on codex-lb 1.22.0: valid inline PNG → websocket
      upstream with ~89% cache on the second identical request; invalid
      base64 → HTTP 400 `invalid_value` fast-fail; 1x1 PNG → HTTP transport.
