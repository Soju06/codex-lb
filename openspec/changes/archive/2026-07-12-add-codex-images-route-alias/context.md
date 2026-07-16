# Context: add-codex-images-route-alias

## Purpose

Codex's built-in `imagegen` tool performs both standalone image generation and
reference-image edits against the model provider `base_url`, not against
`/v1`. codex-lb documents `base_url = "http://127.0.0.1:2455/backend-api/codex"`
for Codex clients, so the tool POSTs to `/backend-api/codex/images/generations`
and `/backend-api/codex/images/edits`. Before this change only `/v1/images/*`
had handlers, so Codex edit requests returned `405 Method Not Allowed`.

## Verified upstream wire contract (openai/codex @ rust-v0.144.1)

All citations are at tag `rust-v0.144.1` (commit
`44918ea10c0f99151c6710411b4322c2f5c96bea`, published 2026-07-09).

### Endpoint paths — relative to `base_url`, not `/v1`

- `codex-rs/codex-api/src/endpoint/images.rs:33-54` posts to
  `images/generations` and `images/edits` joined onto the provider base URL
  (`codex-rs/codex-api/src/provider.rs:53-73`, `url_for_path`:
  `base.trim_end_matches('/') + "/" + path.trim_start_matches('/')`).
- Codex's own integration test asserts the joined paths
  `/api/codex/images/{generations,edits}` when `base_url = "<server>/api/codex"`
  (`codex-rs/app-server/tests/suite/v2/imagegen_extension.rs:160,496,550,562`).
- The client never sends a trailing slash (`url_for_path` joins with exactly
  one separator). In codex-lb the trailing-slash variants are not aliased:
  they fall through to the SPA `GET /{path:path}` catch-all
  (`app/main.py`), so a POST returns 405 with the OpenAI error envelope —
  identically on `/v1/images/*/` and `/backend-api/codex/images/*/`.
  Regression tests pin this parity.

### Request encoding — JSON, never multipart

- `EndpointSession::execute` wraps the serialized struct as
  `RequestBody::Json` (`codex-rs/codex-api/src/endpoint/session.rs`), sent
  with `Content-Type: application/json` and no compression
  (`codex-rs/codex-api/src/provider.rs:83`). Multipart exists in the client
  only for realtime calls, never for images. This is why the Codex-base edit
  alias is a JSON adapter rather than a rebind of the multipart `/v1` handler.

### Exact request shapes (`codex-rs/codex-api/src/images.rs`)

- `ImageGenerationRequest`: `prompt`, `model`, optional
  `background|n|quality|size` (omitted when `None`).
- `ImageEditRequest`: `images: Vec<ImageUrl>` where
  `ImageUrl { image_url: String }` carries a base64 data URL, plus `prompt`
  and the same optional scalars. Codex's unit test pins the exact wire body
  (`codex-rs/codex-api/src/endpoint/images.rs:259-267`):

  ```json
  {"images": [{"image_url": "data:image/png;base64,Zm9v"}],
   "prompt": "add a red hat", "model": "gpt-image-1.5"}
  ```

- The shipped tool always sends `model: "gpt-image-2"`,
  `background/quality/size: "auto"`, omits `n`, and caps edits at 5 images
  (`codex-rs/ext/image-generation/src/tool.rs:57,270-327`).
- Images are always `data:<mime>;base64,...` data URLs — files go through
  `into_data_url()` (`codex-rs/utils/image/src/lib.rs:53-56`) and the
  app-server rejects remote `http(s)` image URLs at input
  (`codex-rs/app-server/src/image_url.rs:4-8`). The adapter's data-URL regex
  (`app/modules/proxy/images_service.py`) matches this output exactly.
- **The client has no mask capability**: `ImageEditRequest` has no mask field
  anywhere in `codex-rs`. The adapter's hardcoded `mask=None` is therefore the
  full contract, not a gap.
- The client never sends `stream`, `output_format`, `response_format`,
  `user`, `moderation`, `input_fidelity`, or `partial_images`; the adapter
  accepts them with `/v1`-compatible defaults as a harmless superset.

A captured real request from Codex Desktop 0.144.0-alpha.4 (PR #1160
discussion) confirms the same shape live:
`POST /backend-api/codex/images/edits`, `Content-Type: application/json`, body
`{"model": "gpt-image-2", "prompt": ..., "images": [{"image_url":
"data:image/png;base64,..."}]}`.

### Response shape expected by the client

`ImageResponse` (`codex-rs/codex-api/src/images.rs:55-70`) requires `created`
and non-empty `data[].b64_json`; `background`, `quality`, `size` are optional
and unknown extras (`usage`, `revised_prompt`) are ignored by serde. codex-lb's
`V1ImageResponse` (`app/core/openai/images.py`) satisfies this. The tool
renders `data[0].b64_json` as `data:image/png;base64,...`, matching the
adapter's default `output_format="png"`.

## Decisions

- **Aliases, not new routes**: `include_in_schema=False` keeps `/v1/images/*`
  the only OpenAPI-visible surface; the Codex-base paths are a transport
  compatibility shim.
- **Generation alias reuses the `/v1` handler unchanged** because Codex's
  generation body is a strict subset of `V1ImagesGenerationsRequest`
  (`extra="ignore"`).
- **Edit alias adapts JSON to the shared pipeline**: decode
  `images[].image_url` data URLs to `(bytes, mime)` tuples and delegate to
  `_proxy_images_edit_request` so validation, auth, account routing,
  observability, and error envelopes stay identical to `/v1`.
- **Observability parity**: `started_at` is captured at handler entry and all
  early-400 paths call `_record_images_edit_early_rejection`, matching the
  `/v1` edit route's metrics contract from #1123.

## Failure modes

- Non-JSON or invalid-UTF-8 body → 400 `invalid_request_error` with
  `param=prompt` (matches `/v1` envelope; `UnicodeDecodeError` is caught
  alongside `JSONDecodeError`).
- Missing/empty `images`, non-object entries, or non-data-URL `image_url` →
  400 with `param=images`.
- Theoretical divergence: `background: "transparent"` is representable in the
  client's types but never sent by the shipped tool; codex-lb rejects it for
  `gpt-image-2` per its validation matrix. Only reachable by non-official
  callers.

## Concrete example

```http
POST /backend-api/codex/images/edits HTTP/1.1
Content-Type: application/json

{"model": "gpt-image-2", "prompt": "make it green",
 "images": [{"image_url": "data:image/png;base64,iVBORw0KGgo..."}],
 "background": "auto", "quality": "auto", "size": "auto"}
```

→ 200 `{"created": ..., "data": [{"b64_json": "..."}], ...}` produced by the
same upstream pipeline as `POST /v1/images/edits` multipart.
