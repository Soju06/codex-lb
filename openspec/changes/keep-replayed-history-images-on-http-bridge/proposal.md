# Change: keep-replayed-history-images-on-http-bridge

## Why

The HTTP responses bridge is bypassed for any request whose `input` contains an
`input_image` part anywhere, at any depth
(`_responses_request_contains_input_image`). Clients that resend the full
transcript on every turn, with no `previous_response_id`, keep an old screenshot
in a replayed `function_call_output` until compaction drops it. After one image,
every later turn of that session misses the bridge, and so misses its upstream
WebSocket session reuse and prompt-cache continuity.

Observed on a deployment (request logs since 2026-09-24, same prompt-size
bucket, one full-history client):

- bridged requests cached 0.81–0.91 of input tokens on `gpt-6-luna`;
- bypassed requests cached 0.42–0.55 on `gpt-6-luna` and 0.06–0.35 on
  `gpt-6-sol`;
- bypassed share: 13.8% of that client's Luna requests (5,649 / 41,026) and
  40.5% of its Sol requests (3,719 / 9,176).

One thread made 12 bridged requests. The first request that carried a
screenshot in a replayed tool output then logged `bypass reason=image`, and the
thread never returned to the bridge for the rest of its life (9+ hours). Issue
#2425 reports the same bypass on Codex Desktop: during overload waves,
image-bearing requests failed 35.3% of the time against 4.3% for bridged
requests.

The bypass was added for one hazard (#903): an invalid *new* inline image held a
bridge pending slot until local timeout instead of failing fast. Upstream has
already accepted a historical inline image in an earlier turn, so replaying it
does not create that hazard. The bridge and upstream WebSocket carry inline
`data:` images unchanged (#2386). Two image hazards are real and stay: an
external image URL, which the upstream WebSocket does not accept, and the
`image_generation` tool.

## What Changes

- The image bridge bypass fires only for:
  - an `input_image` whose `image_url` is still an external `http(s)` URL,
    anywhere in `input`, including in history;
  - any `input_image` in the current turn, meaning the items after the last
    model-output item;
  - a request that declares the built-in `image_generation` tool.
- A historical inline `data:` image before the last model-output item, including
  one nested in a replayed tool output, no longer keeps a request off the
  bridge on its own.
- The routing counter reason (`reason="image"`), its log line, and the
  upstream-transport precedence are unchanged. No new setting, metric, or label.

## Capabilities

### Modified Capabilities

- `responses-api-compat`: `Requirement: Responses input images bypass the HTTP
  bridge` is replaced in place by the narrowed rule (see the delta spec). No
  other requirement changes.

## Superseded requirement text (for the PR body)

`openspec/specs/responses-api-compat/spec.md`,
`### Requirement: Responses input images bypass the HTTP bridge`, as of
`09a140fa9`:

> The service MUST bypass the HTTP responses bridge when a `/v1/responses`,
> `/backend-api/codex/responses`, `/responses/compact`, or `/v1/responses/compact`
> request contains any `input_image` part in top-level input items, nested
> message content, or tool output content, and send the request over the raw
> (non-bridge) Responses stream path. This bypass MUST happen after rejecting
> unsupported uploaded-image references and MUST be limited to the current
> request; subsequent text-only requests MAY continue using the HTTP responses
> bridge.
>
> The raw (non-bridge) path is the source of truth for image validation and
> upstream image error semantics. The bridge MUST NOT hold image requests waiting
> for `response.created` when upstream rejects an invalid inline image payload.

## Out of scope

- The request-log `upstream_transport` label for bypassed requests. It is a
  separate change: `record-resolved-upstream-transport-label`.
- Widening the bridge's image inliner or its shallow post-inline guard
  (`_count_external_image_urls`). The new bypass sends every external URL,
  however deeply nested, to the raw path before that guard runs.
- Bridge saturation-based image admission, and output-free overload failover on
  the direct path (#2351).

## Impact

- Full-history sessions with an old screenshot return to the bridge, so
  `codex_lb_http_bridge_routing_total{stage="bypass",reason="image"}` falls and
  bridge reuse rises for that traffic.
- A request whose current turn carries an image still bypasses for that one
  request, as before.
