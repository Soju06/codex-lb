# Routing Strategy Guide

The dashboard setting **Routing strategy** controls how eligible accounts are selected for each request. No strategy can guarantee account-safety outcomes; conservative use still depends on staying within OpenAI terms, using normal request volumes, and avoiding traffic patterns that would be unusual for your accounts.

For low-volume, policy-compliant personal use, start with **Capacity weighted** or **Relative availability** and keep sticky threads enabled. Those strategies preserve session locality while avoiding sudden all-traffic shifts to a single account.

| Routing strategy | Behavior | Trade-offs and recommended use |
|---|---|---|
| Capacity weighted | Prefers accounts with more usable quota headroom. | Good default for mixed pools and normal compliant usage. |
| Relative availability | Draws from the strongest available accounts with configurable weighting. | Smooths distribution while still preferring healthier accounts. |
| Usage weighted | Reacts to observed recent usage. | Useful when usage history should influence selection, but less direct than capacity-based routing. |
| Round robin | Cycles evenly through eligible accounts. | Simple and predictable, but ignores quota shape and reset timing. |
| Fill first | Uses one account heavily before moving on. | Best for controlled drain tests; less conservative for everyday traffic. |
| Sequential drain | Drains accounts in a fixed order. | Useful for maintenance or explicit account rotation, not a normal safety-first default. |
| Reset drain | Prioritizes capacity near reset windows. | Helps consume expiring quota, but can create timing-shaped bursts. |
| Single account | Pins all traffic to one selected active account. | Useful for isolation and debugging; no load balancing. |

Change the strategy live in the dashboard under **Settings → Routing** — no restart required.

## Routing, quotas, and eligibility explainer

### Account eligibility vs displayed status

An account's badge (`Active`, `Paused`, `Limited`, …) is its **displayed status**, derived from the durable account state plus current usage. Eligibility is decided **per request**: the selector can skip an `Active` account because of a cooldown, error backoff, a quota threshold or exhaustion, model/plan incompatibility, or because a thread's continuation state is owned by a different account. `Active` therefore does not mean "will serve the next request".

### Soft sticky routing vs hard Codex continuation affinity

These are two different mechanisms:

- **Soft sticky routing** (the `Sticky threads` toggle and session/thread locality) is a *preference*: keep requests for the same session on the same account when possible, mostly to preserve warm upstream prompt caches. When the preferred account is unavailable or over the sticky thresholds, traffic can move.
- **Hard Codex continuation affinity** binds a request to the account that owns its continuation state — an explicit Codex turn state, a stored `previous_response_id`/conversation, or uploaded file ids. This binding is **not controlled by `Sticky threads`**: turning the toggle off does not make owner-bound requests portable. codex-lb releases the binding only when it can prove the request is a safe, account-neutral replay (or the continuation is migrated).

If a thread's owner account becomes unavailable, requests that still require that owner can fail with `No available accounts` even though the rest of the pool is healthy. Starting a fresh thread (no continuation state) routes normally.

### Primary vs secondary quota, used vs remaining

- **Primary quota** is the short **5-hour** usage window.
- **Secondary quota** is the longer window: **weekly** on most plans, or **monthly** on plans that report only a monthly window (the monthly window is normalized into the secondary slot for routing).

Account pages display each window as **percent remaining**; the sticky reallocation thresholds in Settings are **percent used**. A `Sticky secondary threshold` of `70` means "move sticky sessions off an account once more than 70% of its secondary (weekly or monthly) window has been used" — in quota terms, once less than 30% remains. Note that routing evaluates thresholds against reported usage **plus temporary in-flight pressure** (concurrent requests and leased tokens), so reallocation can begin slightly before the raw account-page numbers reach the threshold. The size of that pressure (in-flight penalty per request, leased-token weight), the account lease TTL, the overload isolation window and the error-rate weighting switch are dashboard settings under **Settings → Advanced → Routing weights and overload isolation**; a field left empty inherits the environment value or the default.

### Prefer earlier reset

When enabled and several accounts are otherwise eligible, selection is restricted to the accounts whose selected quota window (5h or weekly) resets soonest. Weekly resets are compared in whole-day buckets; when the selected window has no known reset time, the other window is used as a fallback. The preference applies to the `Capacity weighted`, `Usage weighted`, and `Fill first` strategies; the fixed-order and draw-based strategies (`Round robin`, `Relative availability`, `Sequential drain`, `Reset drain`, `Single account`) ignore it.

### Relative latency weighting

The `Capacity weighted` and `Relative availability` strategies also discount accounts that are slower than their siblings, down to half of their normal weight, using two replica-local signals measured from the last hour of successful, unqueued, single-attempt turns:

- **First-token latency** per account (measured from the upstream send, so the proxy's own pre-send work is not the account's), on small, low-effort turns: an account more than 15% above the fleet median is discounted.
- **Output throughput** (tokens per second from the first token to the upstream's terminal event as it was parsed -- downstream delivery and local settlement time are not generation time) per account **and per model**, on turns with at least 200 output tokens: an account more than 15% below the fleet median *for the model being requested* is discounted for that model only, so an account that streams slowly on one model keeps its full weight on the others.

Each signal needs at least eight samples on at least three accounts (per model, for throughput) before it acts and is neutral when the whole fleet is equally slow. When both apply, the smaller multiplier is used, never their product. The weight never excludes an account and never moves an established sticky session; there is nothing to configure.

### Limit warm-up

Limit warm-up sends **one small real request** (using the configured warm-up model and prompt) to an opted-in account when one of its quota windows is confirmed to have newly reset, verifying that the account responds. It consumes a small amount of quota. The optional staggered idle mode additionally pre-starts the 5h window of idle opted-in accounts before traffic arrives; the configured cooldown applies to these staggered idle probes, while ordinary reset-confirmed probes fire once per confirmed reset. Accounts opt in individually (`Enable warm-up` in account actions); the last attempt's result, model, and time are shown on the account list entry.

## Direct model-source compaction

For an external model served by a Responses-compatible source, explicit requests to
`/v1/responses/compact` and `/backend-api/codex/responses/compact` forward to that
source's `/responses/compact` endpoint. No subscription account is required. Native
continuation and uploaded-file ownership still take precedence. The source must
support compaction; its errors are returned without switching models or falling
back to subscription accounts. This does not enable terminal `compaction_trigger`
requests or compaction for overflow-pinned conversations. Owning spec:
[responses-api-compat](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/responses-api-compat).

## Subscription-exhaustion overflow to a model source

> **Status: shipped, ship-dark.** Overflow routing is in this release and inactive until a source is designated: with the control **Off** (both settings columns `null`) the request path performs no probe, pin lookup, source selection or body walk and an exhausted pool answers exactly as before, byte for byte. **Production flip gate:** do not designate a source in production before every replica runs the release that contains the wired anchors and the WebSocket parity (this one); rehearse on a canary first (see [Canary and drills](#canary-and-drills)). Owning spec: [model-source-routing](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/model-source-routing); design: issue [#2123](https://github.com/Soju06/codex-lb/issues/2123), which supersedes the earlier proposals in #428 and #1664.

**Settings → Routing → "Overflow to model source when all subscription accounts are exhausted"** designates one OpenAI-compatible model source with Responses support as the place new requests go when the whole subscription pool is out of usage. It is **orthogonal to `Routing strategy`**: the strategy still decides which subscription account serves a request while any account can; overflow only answers the question "what happens when none can". Default **off**; the setting names an operator-owned paid source, so it can never be a default.

### Trigger

Overflow triggers only on **pool-wide usage exhaustion** — the exact condition that today returns `429 usage_limit_reached` with `resets_at` (every eligible account is `QUOTA_EXCEEDED` or `RATE_LIMITED` with used ≥ 100 % evidence). It never triggers on per-account capacity caps, API-key fair-share throttles, transient upstream errors, authentication failures, or a single account's rate limit: those keep their current answers.

### Eligibility

- New conversations, or requests without prior reasoning, on standard Responses models. The **gpt-5.6 family cannot overflow in this version** (its Responses-Lite / code-mode request bodies are not portable to an OpenAI-compatible source); the preflight marks those models.
- Unscoped API keys only. A key scoped to a set of sources already routes to them directly and never overflows; the preflight counts such keys.
- Background jobs never overflow, except Codex's user-initiated `review`, `compact` and `collab_spawn` subagents; a memory-generation request (`x-openai-memgen-request`) never does. Requests bound to a ChatGPT account mid-turn (a binding `x-codex-turn-state`) and native Codex requests without a `thread-id` keep today's `429`.
- The trigger and every decline are decided once, at route admission, before any account is selected or reserved. Exhaustion discovered later in a turn keeps today's terminal answer; the next request overflows.
- Declare the non-function tool types your source accepts (`custom`, `apply_patch`, `web_search`, `shell`, `tool_search`) on its model entries (`supports_search_tool`, `experimental_supported_tools` in the model's raw metadata). The preflight lists the undeclared types per model, together with missing vision, missing streaming, missing pricing (the cost tile shows $0) and a context window that is missing or smaller than the registry's (Codex compacts on the registry's arithmetic, so a smaller window fails every turn with a pre-stream 400).

### Stickiness and the drain window

A conversation that received source output is **pinned** to the source and stays there (7-day idle limit) from any Codex process, `codex resume` or API key -- even after the subscription pool recovers. The pin is written together with the source's `response.id` anchor (for SDK `previous_response_id` chains) before the first content-bearing frame reaches the client, so a failed or abandoned attempt leaves the conversation on subscription. The request the source receives carries the client's own `store` (omitted when the client omitted it; Codex's `store: false` verbatim), not the `store: false` the ChatGPT backend requires, so an SDK chain is actually stored where its anchor points; an SDK request sent with `store: false` is not anchored. A pinned conversation may switch to any model the source serves; a model another model source serves directly is still decided by the pin -- served by the pinned source when it lists the model, otherwise treated as a model the pinned source does not serve, so the other source never receives the pinned transcript; a model the source does not serve, a source that is disabled or deleted, and an expired pin end reasoning-bearing conversations with `400 subscription_overflow_source_unavailable` (or `subscription_overflow_unsupported_input` for an expired one) and return the others to subscription once the pin is durably deleted. An image (`input_image`) on a pinned conversation needs the source model's vision (`supportsVision` on the model entry, which the preflight warns about); otherwise the turn is refused with `400 subscription_overflow_unsupported_input` naming images, and the pin is kept. **Compaction is unavailable on the source**: `/responses/compact` on a pinned conversation is refused with `400 subscription_overflow_unsupported_input`, and Codex has no local fallback, so a pinned conversation that fills its context must be restarted (forwarding compaction to the source is planned for a later version). Turning overflow **off** stops fresh overflow immediately and arms a drain deadline: conversations already on the source keep working for at most 7 more days, then expire. While that is running the dashboard shows the date by which every pinned conversation has expired (`subscriptionOverflowPinsExpireBy`, the switch-off time plus 7 days) — not the drain deadline itself (`subscriptionOverflowDrainUntil`, the switch-off time plus 29 days), which additionally spans the 21-day tombstone grace during which an expired conversation is still recognised as pinned and handled like one on a disabled source instead of being treated as new. Designating a source again clears both.

### Kill switches

| Action | Effect |
|---|---|
| Set the control to **Off** | No new overflow; pinned conversations drain for ≤ 7 days. |
| Disable the source (or the model on it) | Immediate. Reasoning-bearing pinned conversations end with an error; the others return to subscription accounts. |
| Delete the source | Clears the designation and arms the drain window in the same transaction; pinned conversations behave as for a disabled source. |

### Client behaviour

- **WebSocket.** Sources are served over HTTP only. A handshake carrying pin evidence (a live pin, an expired pin, or a bounce from the last 60 s) is denied with HTTP `426` (`subscription_overflow_requires_http_transport`), which makes Codex switch **that session** to HTTP until the process restarts -- this needs **Codex 0.99.0 or newer** (0.93-0.98 switch only after their WebSocket retry budget, about 6 s; 0.92 and older never use WebSocket). A handshake without evidence is accepted even when the pool is exhausted. When exhaustion hits during an open session, Codex receives an in-band `503 model_source_requires_http_transport` event and the proxy records a 60 s bounce, so Codex's automatic retry re-handshakes, meets the `426` and completes the turn over HTTP; a pinned conversation on an open socket is bounced the same way. The HTTP bridge never enters overflow.
- **Hint on the 429.** When a conversation cannot overflow only because it already contains prior reasoning, the `429` carries a hint: native Codex shows "You've hit your usage limit. Start a new conversation to continue on the configured overflow model source, or try again at ..." (header `x-codex-promo-message`); SDK clients get the sentence appended to `error.message`. No other decline carries a hint, and the rest of the answer is byte-identical to today's.
- **Headers toward the source.** Requests to the source carry only headers the proxy constructs (`Authorization` from the source credential, `Content-Type`, `Accept`); no client or ChatGPT-internal header (`session-id`, `thread-id`, `x-codex-*`, `x-openai-subagent`, `chatgpt-account-id`, the client's `authorization`, ...) is forwarded -- for direct routing and overflow alike.
- Codex retry semantics for answers on this path: `429` is not retried (`retry_429: false`) and surfaces as "exceeded retry limit, last status: 429" — a source's own rate limit is therefore visible only as that message; `5xx` is retried (`retry_5xx: true`) through the usual reconnect ladder; `400 invalid_request_error` is rendered immediately.
- `/status` shows the subscription pool, not the source. Forked conversations may replay reasoning the source cannot read. While overflow is on or draining, routing depends on the proxy database being reachable: every native request with a `thread-id` and every `previous_response_id` request performs one bounded pin read (also when a model source serves the requested model directly), and a database outage fails those requests closed with a fast `503 model_source_unavailable` (WebSocket handshakes with `426`) rather than sending a possibly source-served conversation to a subscription account; requests without either header are unaffected.
- Transient trouble on a pinned conversation (source breaker open after 3 failures, source at `max_concurrency`, pin read timeout) answers `503` with `Retry-After`, never `429`; a fresh request in the same situation simply keeps today's `429`. After the 30 s open window one request is admitted as the trial; the breaker closes again as soon as that trial yields its first output item (other requests flow while it is still streaming), and a stream that stalls or drops after its first item counts as one failure toward the next trip.
- Limited API keys stream live and are charged an estimate when the source omits usage or the client disconnects mid-answer; fast mode is served at standard tier on the source.

### Note on the bridge's usage-limit answer

Since the WP-E fix in #2124 the HTTP Responses session bridge returns the pool's `429 usage_limit_reached` immediately instead of waiting out the capacity window, so a client sees the same exhaustion answer on both the direct and the bridged path. Overflow replaces that answer on the direct HTTP routes only; the bridge keeps it.

### Observability

- `codex_lb_subscription_overflow_total{route,outcome}` counts every decision: `dispatched_fresh|pinned|anchor`, `declined_<reason>`, `pinned_unservable_*`, `pinned_released_neutral`, `pinned_lookup_timeout`, `pin_commit_failed|unverified`, `bounced_ws_handshake|event`, `decision_error`; `codex_lb_model_source_breaker_state{source_id}` (0 closed, 1 open, 2 half-open); `codex_lb_model_source_live_pins{kind}`, sampled once per hourly retention tick on the leader replica; `codex_lb_model_source_dispatch_total{kind=fresh|pinned|anchor}`.
- Request-log rows for overflow attempts carry `source` `subscription_overflow` (fresh) or `subscription_overflow_pinned` (pinned and anchored), no account, the source id, `requested_service_tier` and a null `service_tier`; estimates charged to limited keys are never written as usage. The dashboard chip, `source` filter and cost tile land with WP-G.
- Log lines: `subscription_overflow_dispatched`, `subscription_overflow_pinned_unservable cause=…`, `subscription_overflow_pinned_released_neutral`, `model_source_breaker state=…`, `subscription_overflow_decision_error stage=…`, `model_source_pin_write outcome=…`. Configuration-class declines (undeclared tool types, unknown fields, Lite bodies) log a rate-limited WARN naming the offending type or field.

### Canary and drills

The designation is one fleet-wide settings row -- there is no per-replica flag -- so a canary that shares the production database would flip production. Run the canary as a **separate deployment with its own database**, designate a **low-limit, priced** source that serves a **standard-Responses** registry model (never a gpt-5.6 / Responses-Lite one), exhaust the canary's subscription pool, and complete this list before the production flip:

| Drill | How | Expected |
|---|---|---|
| Disconnect | Press Esc in Codex while the source is still producing its first token. | One request-log row with status `cancelled`, no pin, source slot and API-key reservation released. |
| Stall | Point the source at a black-holed address. | `504 model_source_timeout` within 60 s before any `200`; the breaker opens within three attempts (fresh requests fall back to today's `429`); ChatGPT traffic and its connector are unaffected. |
| Silent headers | Source answers `200` and then nothing. | `504 model_source_timeout` at 30 s; nothing was sent to the client. |
| Neutral release | Disable the source while a conversation without reasoning is pinned. | Its next turn is served by a subscription account and the pin is gone; a reasoning-bearing conversation gets `400 subscription_overflow_source_unavailable`. |
| Clear-then-touch | Set the control to Off and keep using a pinned conversation daily. | The source serves it until day 7 and never a subscription account; the pin table is empty at the drain deadline. |
| Kill switches | Off; then disable and delete the source. | Fresh overflow stops within the settings-cache window (≤ 5 s) and pinned conversations drain; disabling or deleting releases source-free conversations and ends the others with `400`. |

Also watch `codex_lb_subscription_overflow_total`, the request-log rows and the source cost over one usage-reset cycle; confirm a pinned conversation survives the pool's reset, `codex resume` and an API-key rotation, then tombstones after 7 idle days; and profile only with py-spy at 10-20 Hz in short bursts (higher rates stall a two-core host). Codex on the canary must be **0.99.0 or newer** for the WebSocket downgrade. Only after the drills pass, and once every production replica runs this release, designate the source in production.

---

*Specs: [account-routing](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/account-routing) · [frontend-architecture](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/frontend-architecture) · [model-source-routing](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/model-source-routing) · [usage-refresh-policy](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/usage-refresh-policy)*

## HTTP to WebSocket promotion

Owning spec: [Responses API compatibility](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/responses-api-compat).

With automatic upstream transport and the default `smart` HTTP policy, Responses
and subscription-backed Chat Completions can reuse upstream WebSocket connections
when requests carry response/cache/session identifiers, a `conversation`, tool
results, or an assistant response followed by new user input. A first request
containing only a user message remains HTTP. Native Codex HTTP callers follow
this policy too; their User-Agent alone does not indicate a WebSocket failure.

Real recent upstream WS failures temporarily keep requests on HTTP (the existing
60-second cooldown). Explicit HTTP policy, image-capable requests and oversized
payloads also bypass the bridge. Source-routed Chat requests keep their source.

Clients resending full history need not retain response headers to reuse a
connection. Inferred locality uses complete initial user input and instructions,
scoped to the API key. It remains a connection preference: the full history is
preserved, and an inferred key never authorizes response-anchor injection.

The dashboard's HTTP badge describes client-to-LB transport; the upstream field
shows the LB-to-provider transport. `codex_lb_http_bridge_routing_total` separates
`admission` from `bypass` with bounded reasons such as `smart_history`,
`smart_tool_result`, `smart_single_turn`, `recent_ws_failure`, `payload_size` and
`image`. Structured `http_bridge_routing` logs include the request ID.
`codex_lb_http_bridge_connections_total{event="reuse"}` measures actual connection
reuse; admission counts are not successful-connection counts. Existing TTFT and
queue latency metrics should be compared alongside reuse when measuring benefits.
