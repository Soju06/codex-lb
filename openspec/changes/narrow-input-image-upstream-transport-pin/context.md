# Context: narrow-input-image-upstream-transport-pin

## Provenance: how a bridge bypass became a transport pin

`bcd63c827` introduced the `input_image` bypass. Its proposal text is entirely
about HTTP bridge pending slots: an image request that sat in the bridge
occupied a pending slot until it timed out (#903), so image requests were routed
to the raw (non-bridge) Responses path. The commit implemented that by setting
`force_upstream_stream_transport = "http"` alongside the bypass, conflating two
separate decisions — *which local path serves the request* and *which upstream
transport it opens*. The spec sentence "and send the request over the raw HTTP
Responses stream path" is the textual origin of the same conflation; it now
reads "the raw (non-bridge) Responses stream path".

Later, `2e124df8f` (#1093) folded `input_image` into the `image_bypass` variable
at the second site, so the same conflation reached the raw path's own transport
resolution.

## Why two sites, and why both had to change

Site A is `_stream_http_bridge_or_retry`
(`app/modules/proxy/_service/http_bridge/streaming.py`). It computes the pin
unconditionally — not gated on `runtime_config.enabled`, unlike the bridge
bypass on the next line — and forwards it to `_stream_with_retry` as
`upstream_stream_transport_override`. It governs `/v1/responses`,
`/backend-api/codex/responses`, and bridge-active `/v1/chat/completions`.

Site B is `_stream_with_retry` (`app/modules/proxy/_service/streaming/retry.py`),
which runs exactly when that override is `None`. It governs everything site A
never touches: `/v1/chat/completions` with the bridge inactive,
`_collect_responses`, and `prefer_http_bridge=False` callers.

At site B the effective pin was the `has_image_generation_tool=image_bypass`
argument, not the explicit re-pin two lines below it. Under the default
`upstream_stream_transport = "auto"`, `_resolve_stream_transport` returns
`"http"` on that argument before the explicit re-pin runs, so a patch touching
only the re-pin would have shipped a no-op. Both sub-edits are load-bearing.
Passing only `image_generation` also restores that parameter's original meaning,
matching its one in-core caller.

## Why the two residual clauses

**Frame budget.** A payload over `_ws_transport_payload_budget_bytes()` (14 MiB)
that reaches the upstream WebSocket goes through
`_prepare_websocket_response_create_payload`, whose slimmer is all-or-nothing:
crossing the budget by one byte replaces *every* historical inline image with
`[codex-lb omitted historical inline image to fit upstream websocket budget]`.
14 MiB, not the 15 MiB `upstream_response_create_max_bytes`, is deliberate:
under `auto` the 14 MiB gate inside `_resolve_stream_transport` already fires
first, so a 15 MiB clause would be unreachable there, and two thresholds for one
routing decision is the drift this change exists to remove. 14 MiB is also
strictly conservative with respect to the 15 MiB slimming trigger.

**External image URL.** `_inline_content_images` silently leaves an external
`http(s)` image URL in place when the fetch fails — non-https, SSRF-blocked
host, non-200, over 8 MiB, or the 8 s timeout — and the raw path has no
fail-closed guard for that, while the HTTP bridge fails closed with a 400
`image_download_failed`. **The evidence that the upstream WebSocket cannot
accept such a URL is a code comment in `request_submit.py` ("silently reject or
hang"), not a captured trace or upstream document.** The clause is kept anyway
because the reported production shape is a pasted screenshot — a `data:` URL,
unaffected — so the clause costs nothing, while dropping it risks an unbounded
hang on a path nobody watches.

## Known bounds

- `_count_external_image_urls` sees top-level `input_image` items and one level
  of `content`, exactly the shapes `_inline_input_image_urls` can rewrite. An
  external URL nested deeper (for example inside a `function_call_output`
  content array) is invisible to both, so such a request can now reach the
  upstream WebSocket with a raw `https://` URL. The blind spot is identical to
  the existing HTTP bridge guard's; no deployment gains a hazard the bridge path
  did not already have, but the raw path had been masked by the pin until now.
  Widening that traversal is deliberately out of scope, so the spec's "external
  image URL still forces HTTP" scenario names the two shapes the guard actually
  reads rather than promising coverage it does not have, and the capability
  `context.md` records the same bound.
- The residual pin still overrides an explicit operator
  `upstream_stream_transport = "websocket"`, so precedence item 2 still beats
  item 1 for these two cases. This inversion is deliberate rather than
  overlooked: with an explicit WebSocket pin `_resolve_stream_transport`
  short-circuits and no size gate runs, so removing the override would turn an
  oversized image payload into a local 400 `payload_too_large` that operators do
  not get today. The spec text now states the wart instead of leaving it
  implicit. It is site A that inverts the precedence, so it applies to every
  request that reaches the bridge routing decision — all of `/v1/responses` and
  `/backend-api/codex/responses`. Site B keeps both residual clauses behind its
  pre-existing `not explicit_transport` gate, so a `/v1/chat/completions`
  request whose bridge admission already declined the bridge follows the
  explicit override instead. That asymmetry predates this change and widening
  site B would be a separate behaviour change; the spec text scopes the claim
  rather than asserting a rule the code does not implement.
- There is no image marker on `request_logs`, so the production error-rate split
  between image and non-image traffic is not derivable from LB telemetry. This
  change is verifiable as a ratio shift on
  `codex_lb_upstream_transport_decisions_total` (`policy="explicit"` falling,
  `"auto"` rising) with `http_bridge_routing{reason="image"}` flat — not as a
  direct WebSocket count.

## Example

Thread turn 7 of a Codex conversation whose turn 1 contained a pasted
screenshot:

```json
{"model": "gpt-5.4", "input": [
  {"role": "user", "content": [{"type": "input_image", "image_url": "data:image/png;base64,..."}]},
  {"role": "assistant", "content": "first answer"},
  {"role": "user", "content": "continue"}
]}
```

Before: the bridge bypass fired *and* `upstream_stream_transport_override` was
`"http"`, so the raw path opened an upstream HTTP POST. After: the bridge bypass
still fires, the override is `None`, and the `smart` policy sees the
assistant-to-user continuation signal and keeps upstream WebSocket with the base
`"auto"` transport mode intact.

## Repository-gate note

`app/modules/proxy/service.py` sits exactly on its 2600-line architecture
ratchet (`openspec/specs/proxy-architecture/spec.md`), which may only be
restored or lowered, and `streaming`/`http_bridge` may not import
`response_create` directly — the façade is the only legal seam. The three
`response_create` image-predicate re-exports were therefore folded into one
plain `# noqa: F401` block, the form this file already uses for its `support`
and `http_bridge.helpers` re-exports. That frees one line, so the file lands at
2599 with the new façade name added.
