# Responses API Compatibility Context

## Purpose and Scope

This capability implements OpenAI-compatible behavior for `POST /v1/responses`, including request validation, streaming events, non-streaming aggregation, and OpenAI-style error envelopes. The scope is limited to what the ChatGPT upstream can provide; unsupported features are explicitly rejected.

See `openspec/specs/responses-api-compat/spec.md` for normative requirements.

## Rationale and Decisions

- **Responses as canonical wire format:** Internally we treat Responses as the source of truth to avoid divergent streaming semantics.
- **Strict validation:** Required fields and mutually exclusive fields are enforced up front to match official client expectations.
- **Cursor alias compatibility:** Cursor UI model labels may append reasoning or speed suffixes to GPT-5 slugs; those are normalized to canonical upstream fields before forwarding.
- **No truncation support:** Requests that include `truncation` are rejected because upstream does not support it.
- **Compact as a separate contract:** Standalone compact is treated as a canonical opaque context-window contract, not as a variant of buffered normal `/responses`.

## Constraints

- Upstream limitations determine available modalities, tool output, and overflow handling.
- `store=true` is rejected; responses are not persisted.
- `include` values must be on the documented allowlist.
- `truncation` is rejected.
- `previous_response_id` is forwarded when `conversation` is absent, but the `conversation + previous_response_id` conflict remains rejected.
- HTTP `/v1/responses` and HTTP `/backend-api/codex/responses` now use a server-side upstream websocket session bridge by default so repeated compatible requests can keep upstream response/session continuity without forcing clients onto the public websocket route.
- Codex-affinity HTTP bridge sessions can optionally use a conservative first-request prewarm (`generate=false`), but that behavior now stays behind an explicit flag so production defaults do not pay an extra upstream request unless operators opt in.
- When operators configure a multi-instance bridge ring, deterministic owner enforcement now applies only to hard continuity keys such as `x-codex-turn-state` and explicit session headers. Prompt-cache-derived bridge keys remain stable for local reuse, but in gateway-safe mode a non-owner replica may tolerate that locality miss and create or reuse a local session instead of failing with `bridge_instance_mismatch`.
- Codex-facing websocket routes now advertise `x-codex-turn-state` during websocket accept and honor client-provided turn-state on reconnect so routing can stay sticky at turn granularity even when the public websocket reconnects.
- HTTP responses routes now also return `x-codex-turn-state` headers so clients that persist response headers can promote later HTTP requests from prompt-cache affinity to stronger Codex-session continuity.
- `/v1/responses/compact` keeps a final-JSON contract and preserves the raw upstream `/codex/responses/compact` payload shape as the canonical next context window instead of rewriting it through buffered `/codex/responses` streaming.
- Compact transport failures fail closed with respect to semantics: no surrogate `/codex/responses` fallback and no local compact-window reconstruction.
- Compact transport may use bounded same-contract retries only for safe pre-body transport failures and `401 -> refresh -> retry`.
- `/v1/responses/compact` is supported only when the upstream implements it.
- `prompt_cache_key` affinity on OpenAI-style routes is intentionally bounded by a dashboard-managed freshness window, unlike durable backend `session_id` or dashboard sticky-thread routing.
- Codex-native direct websocket `/backend-api/codex/responses` treats upstream `previous_response_id` as an ephemeral anchor. If that anchor goes stale, the proxy masks raw upstream details and emits the sanitized canonical `previous_response_not_found` classifier so compatible Codex clients can retry with full local history and no `previous_response_id`. The upstream Codex socket has emitted this condition both with the canonical code and as a parameterless `invalid_request_error` carrying ``Invalid `previous_response_id`.``; both shapes use the same recovery policy.
- Upstream Responses WebSockets use transport ping/pong control frames to detect a black-holed connection without confusing valid application-event silence with an idle turn. Direct and routed connections reuse `proxy_downstream_websocket_idle_timeout_seconds` for this zero-config liveness budget.
- A post-send liveness timeout is delivery-ambiguous. It remains account-neutral, is never transparently replayed, and retires the affected upstream socket so a client retry opens a fresh route without risking duplicated model work or tool side effects.
- An HTTP SSE first-event `stream_idle_timeout` is also account-neutral for health writes. The request may still exclude that account and fail over, but idle silence must not increment `error_count` or move the account into probe/drain.
- HTTP bridge settlement ownership is explicit: `closed` rejects new work but does not imply that a submitter owns existing siblings. Only a liveness-failed send claims whole-deque settlement under the lifecycle lock; otherwise the reader remains responsible for settling pending requests when the transport dies.
- A DRAINING durable row with a live lease is still owned. Foreign `claim_live_session` and local session create must not steal it, including when forced recovery would otherwise run because the owner endpoint is missing; expired or ownerless DRAINING rows remain recoverable.
- Hard-affinity retry-circuit evidence is request-lifecycle evidence: retirement counts only while the bridge still owns an eventless pending request. Idle no-pending retirement remains observable but neutral, so routine socket churn cannot manufacture the first strike for a later real timeout.

## Fast Mode and Service Tiers

codex-lb accepts the OpenAI/Codex `service_tier` field on Responses and Chat
Completions compatible routes. The legacy `fast` spelling is accepted as an
alias and is forwarded upstream as the canonical `priority` tier.

Fast Mode is request-level intent, not a local speed guarantee. The upstream
Codex backend decides the actual tier for each completed response. codex-lb
therefore records three separate values in request logs:

- `requestedServiceTier`: what the client or API key asked for, after alias
  normalization.
- `actualServiceTier`: what upstream reported in the completed response, when
  upstream included it.
- `serviceTier`: the effective billable tier. This uses `actualServiceTier`
  when present and falls back to `requestedServiceTier` only when upstream omits
  the actual tier.

If a request is sent with `service_tier: "fast"` or `service_tier: "priority"`
and the completed row shows `requestedServiceTier: "priority"` but
`actualServiceTier: "default"`, codex-lb forwarded the priority request and
upstream chose the default tier. That can happen even when websocket transport
is active.

For OpenCode or Codex-compatible clients, enable Fast Mode by sending a
Responses request with:

```json
{
  "service_tier": "priority"
}
```

Clients that expose Fast Mode as `fast` may keep using that spelling; codex-lb
normalizes it to `priority` before forwarding.

### Ultrafast Processing

The [OpenAI Responses API reference](https://developers.openai.com/api/reference/resources/responses/methods/create)
documents `ultrafast` as an access-controlled processing tier currently
available for `gpt-5.6-sol`. codex-lb forwards this canonical value unchanged;
it does not grant Ultrafast access by itself.

Account eligibility comes from live or retained per-account upstream catalog
metadata. The bundled bootstrap catalog deliberately does not advertise
Ultrafast. If no account advertises the tier, an explicit Ultrafast request
cannot select an eligible account; API-key enforcement follows the existing
model-capability fallback when the model itself does not advertise the tier.

Send a Responses request with:

```json
{
  "model": "gpt-5.6-sol",
  "input": "Summarize the change.",
  "service_tier": "ultrafast"
}
```

After completion, verify that the response reports
`service_tier: "ultrafast"`. Request logs retain `ultrafast` in the requested,
actual, and effective billable tier fields when upstream confirms it.

### Operator Fast Mode prohibition

Operators can enable the Routing setting `prohibitFastMode` when qualified
Codex harness model aliases such as `gpt-5.6-sol-xhigh-fast` must run at the
normal OpenAI tier. The alias still supplies its canonical model and reasoning
effort, but does not derive `service_tier: "priority"`. This policy does not
rewrite an explicit client tier or an API-key-enforced tier; see
`openspec/specs/fast-mode-policy/context.md` for scope and operating notes.

API keys can also force the tier for traffic that uses that key. Set the key's
enforced service tier to `priority` or `fast`; both values are stored and
returned as `priority`.

To verify a completed Fast Mode request:

1. `Transport` should be `WS` if you are verifying the websocket Codex path.
2. `requestedServiceTier` should be `priority` when the client requested Fast
   Mode or the API key enforced it.
3. `actualServiceTier` is the upstream result. `default` means upstream did not
   grant priority for that response.

This distinction matters for quota and cost accounting: codex-lb prices the
request from the effective billable `serviceTier`, not from the requested tier
when upstream reports a different actual tier.

## Include Allowlist (Reference)

- `code_interpreter_call.outputs`
- `computer_call_output.output.image_url`
- `file_search_call.results`
- `message.input_image.image_url`
- `message.output_text.logprobs`
- `reasoning.encrypted_content`
- `web_search_call.action.sources`

## Failure Modes

- **Stream ends without terminal event:** Emit `response.failed` with `stream_incomplete`.
- **Upstream error / no accounts:** Non-streaming responses return an OpenAI error envelope with 5xx status.
- **Compact upstream transport/client failure:** Retry only inside `/codex/responses/compact` when the failure is safely retryable; otherwise return an explicit upstream error without surrogate fallback.
- **HTTP bridge session closes or expires:** The next compatible HTTP `/v1/responses` or `/backend-api/codex/responses` request recreates a fresh upstream websocket bridge session; continuity is guaranteed only within the lifetime of one active bridged session.
- **Multi-instance routing without bridge owner policy:** if operators do not configure a bridge ring or front-door affinity, continuity can still fragment across replicas. With a configured bridge ring, hard continuity keys landing on a non-owner replica are proxy-forwarded to the owner replica; the proxy fails closed only when the owner endpoint or ring membership cannot be resolved or the forward signature fails authentication. Gateway-safe prompt-cache requests may accept locality misses and continue locally instead of forwarding.
- **Codex websocket reconnects:** Reconnect continuity now depends on the client replaying the accepted `x-codex-turn-state`; generated turn-state is emitted on accept for backend Codex routes and echoed back when the client already supplies one.
- **Codex websocket stale previous-response anchors:** Direct backend Codex websocket stale-anchor failures are either replayed transparently from a self-contained full resend or surfaced as a sanitized `response.failed` whose `response.error.code` is `previous_response_not_found`; the error omits `param`, the raw upstream envelope, and the missing `resp_...` id. A connect-time failure uses the same code directly at `error.code`. This includes the parameterless upstream message ``Invalid `previous_response_id`.``. OpenAI-compatible `/v1/responses` websocket clients continue to receive generic `stream_incomplete` masking.
- **Websocket handshake forbidden/not-found:** Auto transport now fails loud on `403` / `404` instead of silently hiding the websocket regression behind HTTP fallback.
- **Upstream websocket stops answering pings:** Pending direct-WebSocket and HTTP-bridge work fails with `upstream_websocket_liveness_timeout`; the account remains healthy and the request is not replayed because upstream acceptance is unknown.
- **Repeated eventless bridge failures:** Two consecutive request-affecting pre-response failures can open the hard-key cooldown. A successful terminal response clears the state; an idle close followed by one real timeout remains only one strike.
- **Invalid request payloads:** Return 4xx with `invalid_request_error`.

## Error Envelope Mapping (Reference)

- 401 → `invalid_api_key`
- 403 → `insufficient_permissions`
- 404 → `not_found`
- 429 → `rate_limit_exceeded`
- 5xx → `server_error`

## Examples

Non-streaming request/response:

```json
// request
{ "model": "gpt-5.1", "input": "hi" }
```

```json
// response
{ "id": "resp_123", "object": "response", "status": "completed", "output": [] }
```

Cursor-style model alias request:

```json
{ "model": "gpt-5.4-mini-high", "input": "hi" }
```

This forwards upstream as `model: "gpt-5.4-mini"` with `reasoning.effort: "high"`.

Retry-circuit accounting example: an idle bridge closes with `pending=0`, then
the next request times out before `response.created`. The idle close is logged
but contributes no failure; the timeout is the first strike. Only another
consecutive eventless pending failure may open the repeated-failure cooldown.

Stale-anchor recovery example: a reconnect sends a tool-output delta with a
recent `previous_response_id`, and upstream answers
``{"type":"error","status":400,"error":{"type":"invalid_request_error","message":"Invalid `previous_response_id`."}}``.
Because the delta cannot stand alone, codex-lb returns a sanitized
`previous_response_not_found` signal on the Codex-native route so the client can
retry once with full local history. If the original request already contained a
self-contained full resend, codex-lb instead reconnects and replays that body
without the rejected anchor.

## Previous-response replay owner fencing

Removing a stale continuation anchor does not make every retained body
portable. Encrypted reasoning, account-scoped items, file references, and
durable bridge operation identities remain owned by the account that first
received them. The proxy records that dispatch owner and requires it on later
HTTP streaming, HTTP bridge, and direct WebSocket selections.

For example, if account A first receives encrypted reasoning and then returns a
pre-visible Trusted Access or authentication failure, account B must never
receive the retained ciphertext. One forced token refresh may replay the body
on account A; permanent failure or owner unavailability fails closed.

Verified recovery installs a replacement body and updates owner state
atomically. A canonical account-neutral replacement clears the owner and may
use normal failover. A verified nonneutral replacement, including a
Responses-Lite full resend, may replay only on the same owner and preserves the
fence.

HTTP bridge tracing archive IDs do not pin neutral requests. A real durable
`operation_id` does pin the request until an explicit operation-rebind path
replaces that identity. Existing file pins and API-key settlement-before-health
ordering remain independent invariants.

Streaming selection authorizes owner compatibility before opening upstream, but
persists a new owner only after dispatch is observed. A transport failure that
is positively classified as pre-dispatch therefore leaves the body unowned and
eligible for its first real dispatch on another account. Ambiguous failures
remain owner-bound.

## Known Client Integrations (Reference)

Third-party agents that consume the `/v1` Responses surface documented by this
capability (rendered guide: `docs/client-setup.md`). These are configuration
examples against the existing contract, not separate compatibility surfaces:

- **OpenCode** — built-in `openai` provider with a `baseURL` override; uses the
  Responses API path so `encrypted_content` / multi-turn reasoning state is
  preserved (Chat Completions custom providers drop it).
- **OpenClaw** — custom provider with `"api": "openai-responses"` against
  `/v1`; Codex-native provider builds may target `/backend-api/codex` instead.
- **Hermes Agent** (Nous Research) — named custom provider with
  `api_mode: codex_responses` against `/v1`; the responses transport carries
  reasoning state across turns like the OpenCode path.

New client guides added to `docs/client-setup.md` should stay configuration-only
examples of this contract; anything needing new proxy behavior requires its own
OpenSpec change first.

## Operational Notes

- Pre-release: run unit/integration tests and optional OpenAI client compatibility tests.
- Smoke tests: stream a response, validate non-stream responses, and verify error envelopes.
- Post-deploy: monitor `no_accounts`, `upstream_unavailable`, compact retry attempts, and compact failure phases, especially on direct compact requests.
- Post-deploy: monitor HTTP bridge reuse/create/evict/reconnect counts and any `previous_response_not_found` or queue-saturation errors on `/v1/responses` and `/backend-api/codex/responses`.
- Post-deploy: monitor `capacity_exhausted_active_sessions`, Codex-session bridge reuse/evict counts, websocket handshake 403/404 rates after the narrower auto-fallback policy, and backend Codex HTTP vs websocket cache-ratio gaps.
- When tracing compact incidents, confirm that request logs and upstream logs show direct `/codex/responses/compact` usage without surrogate `/codex/responses` fallback.
- Post-deploy: monitor `no_accounts`, `stream_incomplete`, and `upstream_unavailable`.
- Post-deploy: monitor `upstream_websocket_liveness_timeout`; recurring failures indicate a host route, VPN, proxy, or intermediary that black-holes established WebSockets.
- Post-deploy: correlate retry-circuit `opened`, `half_open`, and `reset` events with bridge `pending` and `response_events_seen` diagnostics. An idle `pending=0` retirement must not precede an immediate two-failure cooldown.
- Post-deploy: monitor `previous_response_not_found` on `/backend-api/codex/responses`; recurring spikes show repeated continuity failures, which may come from malformed client identifiers, server-side invalidation, or connection lifecycle. Clients should perform the documented full-context retry without `previous_response_id`. Investigate socket-lifecycle remediation only when a separate close-reason, reconnect, or transport diagnostic correlates with the failures.
- Websocket/Codex CLI tier verification runbook: `openspec/specs/responses-api-compat/ops.md`
