# Summary

## Root cause

This bug was a three-fault chain:

1. `/v1/responses` payloads carrying `{"type":"input_image","file_id":"file_*"}` or `{"type":"input_image","image_url":"sediment://file_*"}` were forwarded upstream even though the Responses surface only accepts inline `data:` URLs for conversation `input_image` parts.
2. codex-lb persisted only `file_id -> account_id`, so after `/backend-api/files/{file_id}/uploaded` completed it had no stored `download_url` / `mime_type` to pull the uploaded bytes back and rewrite them into the codex-style inline image form.
3. When upstream rejected that bad shape, the HTTP responses bridge saw a clean close (`close_code=1000`) with zero `response.*` events and treated it as transient, looping through `retry_precreated` / `retry_fresh_upstream` until the request budget expired.

## What changed

### `app/core/clients/proxy.py`

- Added `_ws_transport_payload_budget_bytes(settings)` so auto transport selection respects the deploy's `max_sse_event_bytes` with 2 MiB headroom for the websocket envelope and control frames.
- `stream_responses()` now computes the post-inline serialized payload size immediately after `_inline_input_image_urls()`, covering both:
  - `app/modules/proxy/service.py::_rewrite_input_image_file_references`
  - `app/core/clients/proxy.py::_inline_input_image_urls`
- `_resolve_stream_transport()` now routes `auto` requests over HTTP before the existing codex-header / model-registry websocket heuristics when that rewritten payload estimate exceeds the websocket budget.
- Explicit `upstream_stream_transport = "websocket"` and `upstream_stream_transport = "http"` still win unchanged.

### `app/core/clients/image_processor.py`

- Added a new codex-faithful prompt image processor.
- Mirrors the upstream codex image contract:
  - accepts only PNG / JPEG / GIF / WebP
  - preserves PNG / JPEG / WebP bytes verbatim when already within 2048x2048
  - re-encodes GIF as PNG
  - resizes oversized images to fit 2048x2048
  - uses JPEG quality 85 and lossless WebP on resized output
- Adds a 32-entry in-process LRU cache keyed by `sha1(bytes) + mode`.

### `app/core/clients/files.py`

- Added `fetch_file_bytes(download_url, expected_mime, max_bytes)`.
- Downloads finalize SAS blobs with a hard byte cap so a single attachment cannot blow the websocket frame budget after base64 expansion.

### `app/core/openai/requests.py`

- Added `_input_image_file_reference()` for:
  - `input_image.file_id`
  - `input_image.image_url = "sediment://file_*"`
- Extended `extract_input_file_ids()` so routing sees both `input_file` and uploaded `input_image` references.
- Added `extract_input_image_file_references()` so the proxy can rewrite only the precise `input_image` parts, without touching any other conversation content.

### `app/modules/proxy/service.py`

- Replaced the old tuple pin with `_FilePinEntry(account_id, download_url, mime_type, file_name, expires_at)`.
- `create_file()` still pins the upload owner immediately so finalize stays on the same upstream account.
- `finalize_file()` now upgrades the pin with `download_url` / `mime_type` / `file_name` once upstream returns `status=success`.
- Pin expiry is clamped to the shorter of:
  - `_FILE_ACCOUNT_PIN_TTL_SECONDS` (30 minutes)
  - the SAS `se=` expiry embedded in `download_url`, when present
- Added `_lookup_file_pin()`.
- Added `_rewrite_input_image_file_references()`:
  - finds only `input_image.file_id` / `sediment://file_*`
  - fetches the uploaded bytes from the pinned SAS `download_url`
  - runs the codex-faithful image processor
  - rewrites the original part to inline `image_url: "data:..."`, preserving `detail` when supplied and defaulting it to `auto` otherwise
  - leaves all non-targeted conversation content byte-for-byte untouched
  - logs a synthetic `image-inline-rewrite` request-log row for observability
- Wired the rewrite into:
  - HTTP `/v1/responses` / backend responses streaming path
  - HTTP bridge path
  - websocket `response.create` prepare path
  - `/responses/compact`
- Added `_classify_upstream_close()` and `response_event_count` tracking.
- HTTP bridge `retry_precreated` now fails fast with `502 upstream_rejected_input` when upstream closes with `close_code=1000` before any `response.*` event.
- `stream_http_responses()` now rewrites uploaded `input_image` references before branch selection, estimates the post-rewrite JSON payload size, and bypasses the HTTP responses bridge per request when that rewritten body exceeds the WebSocket frame budget.
- The bypass uses a local `dataclasses.replace(runtime_config, enabled=False)` copy only, so bridge state stays unchanged globally and smaller follow-up requests still use the bridge normally.

### `tests/unit/test_image_processor.py`

- Added coverage for passthrough, resize, GIF->PNG re-encode, unsupported formats, garbage bytes, ORIGINAL mode, and cache-hit identity.

### `tests/unit/test_files_client.py`

- Added coverage for `fetch_file_bytes()` success and `file_too_large` enforcement.

### `tests/unit/test_openai_requests.py`

- Added coverage for `input_image.file_id`, `sediment://file_*`, and `extract_input_image_file_references()`.

### `tests/unit/test_proxy_utils.py`

- Added coverage for:
  - `_lookup_file_pin()`
  - `_rewrite_input_image_file_references()` single and multiple rewrites
  - missing pin -> `400 file_not_found`
  - oversized download -> `400 file_too_large`
  - preserving non-image conversation content
  - returning the pinned account for routing
  - clean-close classifier
  - HTTP bridge precreated retry suppression on rejected input
  - large rewritten payloads forcing HTTP only in `auto`
  - large rewritten payloads bypassing the HTTP responses bridge selector
  - smaller / unknown payload sizes preserving websocket preference
  - explicit transport overrides still winning
  - websocket budget calculation from `max_sse_event_bytes`

### OpenSpec

- Amended `openspec/changes/add-backend-api-files-protocol/`:
  - `proposal.md`
  - `tasks.md`
  - `specs/responses-api-compat/spec.md`
- Documented accepted `input_file` / uploaded `input_image` shapes, the inline rewrite contract, the 16 MiB cap, the “rewrite only the targeted `input_image` parts” rule, the auto HTTP fallback for oversized rewritten payloads, and the clean-close fail-fast behavior.
- Added the bridge-bypass scenario so the OpenSpec now covers the default bridge-enabled `/responses` path as well as `_resolve_stream_transport()`.

### Dependency / lockfile

- `pyproject.toml` now declares `pillow>=10.0`.
- `uv.lock` was updated so the direct dependency is in sync.
- Pillow was added explicitly even though it was already present transitively because this code now imports `from PIL import Image` directly in production.

## Caveats

- SAS expiry vs pin TTL:
  - file pins now expire at the earlier of 30 minutes or the SAS `se=` timestamp when present
  - if the SAS URL expires before the follow-up `/responses` call arrives, inline rewrite fails closed instead of attempting a stale fetch
- Cache misses:
  - the image processor cache is in-process only
  - a different worker or a cold process simply re-downloads and re-processes the image
- Partial multi-image rewrites:
  - if any referenced upload pin is missing / expired / unfetchable, the whole request fails
  - there is no partial-forward behavior

## Verification

- `uv run --frozen ruff check app tests`
- `uv run --frozen ruff format --check app tests`
- `uv run --frozen ty check app`
- `uv run --frozen pytest tests/unit -q`
- `uv run --frozen pytest tests/integration/test_proxy_files.py -q`
- `uv run --frozen pytest tests/integration/test_proxy_responses.py -q`

## Could not verify

- `openspec validate add-backend-api-files-protocol --strict --no-interactive`
  - the `openspec` CLI is not installed in this workspace (`openspec: command not found`)
