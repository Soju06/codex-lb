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
- Codex-native direct websocket `/backend-api/codex/responses` treats upstream `previous_response_id` as an ephemeral anchor. If that anchor goes stale, the proxy must mask raw `previous_response_not_found` details and emit a sanitized `codex_previous_response_stale` classifier so compatible Codex clients can soft-reset and retry without `previous_response_id`.

## HTTP Bridge Liveness and Durable Anchor Recovery

HTTP bridge heartbeat framing uses client identity separately from event-shape normalization. A verified native Codex Desktop request needs a parsed JSON SSE event before `response.created`, even when its payload also looks OpenAI-compatible and therefore still passes through response-event normalization. Explicit OpenAI SDK fingerprints take precedence and keep comment heartbeats, while public `/v1/responses` continues exposing only OpenAI-contract-safe events.

This split avoids treating payload heuristics or continuity headers as client authentication. It changes only the liveness frame: authentication, validation, routing, fingerprint normalization, and public vendor-event filtering continue through their existing paths. The first generated heartbeat still waits until after the startup-error probe so a local startup failure can retain its HTTP status.

Durable `latest_response_id` is an optimization for automatically reattaching a fresh upstream socket, not an indefinitely trusted conversation record. If an automatically injected anchor produces no response lifecycle event before the owner watchdog expires, the exact anchor is quarantined from later automatic injection. Quarantine retains the prior input count and fingerprint as recovery proof, while historical aliases remain available for explicit continuity lookups and fencing preserves a newer owner or response lineage.

OpenAI's [WebSocket conversation-state guidance](https://developers.openai.com/api/docs/guides/conversation-state#previous_response_id-in-websocket-mode) and [deployment checklist](https://developers.openai.com/api/docs/guides/deployment-checklist#use-websocket-mode) define the upstream boundary: a WebSocket currently has a maximum lifetime of 60 minutes, its most recent response cache is connection-local, and an uncached `store=false` chain must reconnect with `previous_response_id` omitted plus full input context. A response id learned on a closed `store=false` socket is therefore not durable reattach state even if codex-lb persisted it for bridge reuse.

One overnight production aggregate contained 10 client-visible 502 events across seven Codex conversations and three upstream accounts. Every affected conversation had exactly one approximately 240-second `missing_response_created_timeout`. In the later five conversations that timeout immediately followed `stream_incomplete` on the same conversation and account; observed closes included no close frame, code 1000, and code 1001. Active conversations later succeeded and none repeated the missing-created timeout after quarantine. Two early cases followed an intentional local restart, but the later sequence occurred without another process restart. This establishes a two-stage failure: the upstream socket rotates or closes, then codex-lb re-injects that socket's connection-local anchor into the fresh socket.

Disconnect-time quarantine removes the second stage. It first considers an actually sent, unambiguous, proxy-injected `store=false` anchor. The bridge also records whether its latest completed response was created with `store=false` on the current socket, which lets an idle close quarantine that exact response after the request queue is empty. This completion provenance is cleared whenever the upstream socket changes, so a durable id loaded from another socket is not mistaken for local evidence. Both paths use the same owner/epoch/expected-response compare-and-set. A confirmed clear also removes the matching in-memory latest anchor, its socket provenance, and pending-tool metadata; a CAS miss or persistence error leaves local continuity untouched. The request interrupted by the close still fails normally because transparent replay could duplicate model output or custom-tool side effects. The safety benefit is that the client's next verified full-history retry reaches the existing unanchored quarantine guard immediately instead of spending another 240 seconds on the dead anchor.

A crash or process restart cannot run the disconnect handler. The fresh-socket admission rule therefore treats any retained automatic `store=false` response id as routing and recovery proof, not as an id that can be copied into a new WebSocket. The same prefix-fingerprint, completed-output, and self-contained mid-tool predicates used by quarantine recovery decide whether a client-supplied full history may start unanchored. Incremental or unverifiable input fails before the new upstream transport is opened. A reusable local socket, a forwardable live owner, and an explicit client anchor keep their existing continuity paths.

Codex Escape closes the downstream SSE before the upstream peer necessarily emits a close frame. Because bridge detach cancels the reader while retiring that socket, it performs the same fenced anchor quarantine directly before durable release. The next fingerprint-verified same-session full-history turn can therefore establish a fresh unanchored lineage; the interrupted turn itself is not replayed.

Codex may instead compact a long session into an upstream-issued encrypted `compaction` item. The ciphertext intentionally does not match the pre-compaction plaintext fingerprint, so fresh-socket admission recognizes its exact protocol shape as complete context while keeping the durable owner account fixed. For example, `[{"id":"cmp_1","type":"compaction","encrypted_content":"..."}]` can start the replacement lineage without the old response id; a user-authored summary, an empty ciphertext, or a suffix containing an account-scoped file id still fails closed.

The same lineage check is repeated after response-create gate admission. This closes the window where an anchor was valid during request preparation but its socket disconnected while the request waited. The bridge sends the captured unanchored form only when existing replay-safety proof permits it; otherwise it returns the actionable full-resend-required 400 without placing the anchored frame on the replacement socket. When external-image inlining is enabled, request preparation applies it to both the anchored and captured unanchored forms and enforces the serialized-size guard after each transformation. This prevents the late fallback choice from reintroducing an external URL that the upstream WebSocket cannot accept.

Soft prompt-cache affinity remains locality rather than hard conversation ownership. If an idle close quarantines its connection-local response id, the next self-contained prompt-cache request may create a fresh session without that id; retained quarantine proof alone does not turn the soft key into a hard full-history requirement.

For example, after a restart the durable row for a Desktop session may still contain `resp_old` plus a fingerprint of its prior input. A rebuilt self-contained history matching that fingerprint is sent on the fresh socket without `resp_old` and can establish the next lineage. A one-item incremental continuation is rejected with HTTP 400 `continuity_requires_full_resend` before connection creation. The message asks the client to resend complete context in `input` or create a new session instead of reporting another upstream close. If `resp_old` instead completed on a still-running socket and that socket closes while idle, current-socket completion provenance lets the close handler quarantine it immediately.

Codex CLI can retry while a turn is still progressing through tools. A normal rebuilt history may append assistant commentary, a `custom_tool_call`, and its matching `custom_tool_call_output` without yet appending an assistant-final or another user message. For quarantine recovery only, that is valid fresh-context evidence when the durable prefix fingerprint matches, the projected whole history has a self-contained call/output graph, and the projected suffix independently passes strict account-neutral fresh-input validation. The retained prefix may keep its existing `additional_tools` declarations because recovery stays on the durable owner account; it is not made portable across accounts. An orphan output, unresolved call, duplicate id, unsupported/account-scoped suffix item, or ordinary incremental message remains fail-closed. This exception does not broaden cross-account replay.

When an origin replica injected the anchor but another replica owns the bridge, the forwarding HMAC binds that provenance marker. Adding, stripping, or changing it invalidates the structured signature, and marked forwards cannot fall back to an older signature that omitted the security-relevant field.

One Goal-enabled production session exposed why this distinction must be machine-readable. After a single real transport failure quarantined the old lineage, roughly 57 automatic incremental continuations were rejected locally. The former `stream_incomplete` 502 made every rejection look like another transient WebSocket close, so Goal retried without changing the input. The dedicated invalid-request code makes the required client action explicit and leaves genuinely transient owner, network, and general continuity failures on their prior retryable paths.

Operationally, correlate `missing_response_created_timeout` retirement logs with stuck-gate metrics, idle/sent disconnect quarantine outcomes, and fresh-socket continuity rejections. Repeated timeouts with successful quarantine point to upstream acceptance or socket-liveness problems; compare-and-set misses usually mean another owner or response already advanced safely. A fresh-socket `continuity_requires_full_resend` after restart means the client did not supply a verifiable full history, not that the retained response id or identical request should be retried. No migration or new runtime setting is required.

## Shared-Egress WebSocket Disconnect Classification

A later production incident showed that ordinary per-account disconnect handling can amplify one local network fault. Seven Responses WebSockets across four accounts ended within 358 milliseconds after one shared environment-proxy EOF. Three concurrent requests belonged to the same healthy continuity owner, so their independent `stream_incomplete` writes crossed the transient-error threshold and temporarily turned later owner-pinned retries into `previous_response_owner_unavailable`.

The adapter now delays only ambiguous Responses receive failures that have no complete peer close frame. Cross-account evidence on the same credential-safe egress key changes the incident to `proxy_network_unavailable` before the HTTP bridge or direct WebSocket relay reaches health settlement. Once a receive task has entered that bounded decision, a concurrent request, idle, or eventless deadline waits for the observed transport failure to finish classification; a truly silent receive remains governed by its normal deadline. Routed fallback uses the actual endpoint id; environment proxies and direct destinations use parsed endpoint components without credentials. The detailed egress decision and edge cases live in `openspec/specs/outbound-http-clients/context.md`.

This classification does not make the interrupted request safe to replay. The send already completed, so upstream acceptance and tool or model side effects remain unknown. Both bridge and direct-WebSocket paths return the network failure without moving accounts or continuity ownership. A single-account failure, a different egress, an anonymous account, a received close frame, or a Live sideband socket retains the previous `stream_incomplete` and health behavior.

Operationally, a correlated burst should appear as near-simultaneous `proxy_network_unavailable` outcomes for distinct accounts without matching account-health counter increases, even when one affected request reaches its local deadline during the one-second decision window. A named close code or repeated `stream_incomplete` on one account remains evidence for the normal account-specific path. The detector is bounded and process-local, requires no setting or migration, and disappears on restart.

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
- **Automatic anchor requires complete context:** Deterministic quarantine and fresh-socket lineage guards return HTTP 400 `continuity_requires_full_resend` with `param=input`; resend complete context or create a new session instead of retrying the same incremental input.
- **Multi-instance routing without bridge owner policy:** if operators do not configure a bridge ring or front-door affinity, continuity can still fragment across replicas. With a configured bridge ring, hard continuity keys landing on a non-owner replica are proxy-forwarded to the owner replica; the proxy fails closed only when the owner endpoint or ring membership cannot be resolved or the forward signature fails authentication. Gateway-safe prompt-cache requests may accept locality misses and continue locally instead of forwarding.
- **Codex websocket reconnects:** Reconnect continuity now depends on the client replaying the accepted `x-codex-turn-state`; generated turn-state is emitted on accept for backend Codex routes and echoed back when the client already supplies one.
- **Codex websocket stale previous-response anchors:** Direct backend Codex websocket stale-anchor failures are surfaced as `response.failed` / `codex_previous_response_stale` without the raw upstream code or missing `resp_...` id; OpenAI-compatible `/v1/responses` websocket clients continue to receive generic `stream_incomplete` masking.
- **Websocket handshake forbidden/not-found:** Auto transport now fails loud on `403` / `404` instead of silently hiding the websocket regression behind HTTP fallback.
- **Invalid request payloads:** Return 4xx with `invalid_request_error`.

## Error Envelope Mapping (Reference)

- 400 full-resend guard → `continuity_requires_full_resend` / `invalid_request_error`
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
- Post-deploy: monitor `codex_previous_response_stale` on `/backend-api/codex/responses`; recurring spikes mean clients are still relying on stale upstream anchors and should perform the documented full-context retry without `previous_response_id`.
- Websocket/Codex CLI tier verification runbook: `openspec/specs/responses-api-compat/ops.md`
