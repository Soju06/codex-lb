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

## Inspect affinity decisions

Request logs record `sticky_key_source`, `sticky_kind`, and `sticky_key_hash` for Responses and compact traffic without enabling trace logs. Callers with `conversations:read` permission can read them as `stickyKeySource`, `stickyKind`, and `stickyKeyHash` through `GET /api/request-logs`. Responses without that permission hide these fields.

The hash is the first 16 lowercase hexadecimal characters of SHA-256 over the UTF-8-encoded resolved selection key. Compare hashes to identify repeated keys; raw session headers can differ from selection keys. Historical rows and paths without an observation return null. Source `none` means resolution explicitly found no affinity.

Each row keeps its existing meaning: direct streams can emit attempt rows; compact, native WebSocket, and bridge rows describe their final request state. A final row does not enumerate every retry. Existing recovery can clear a key, leaving a null hash with the original source classification. These fields do not by themselves explain account-owner precedence or why an account was skipped.

The columns follow existing request-log retention and never store raw keys or prompts. See the [affinity observation contract](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/proxy-runtime-observability/spec.md) and [query example and privacy notes](https://github.com/Soju06/codex-lb/blob/main/openspec/specs/proxy-runtime-observability/context.md#affinity-decisions-in-request-logs).

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

## Reserve quota on individual accounts

On the **Accounts** page, an account can have an optional maximum-used
percentage. For example, a limit of `10%` reserves roughly 90% of that
account's standard quota for direct use. Optional **5-hour** and **weekly** overrides replace this default for their window. Blank overrides inherit the default; with no default, only explicitly configured windows are limited. Monthly windows use the default, never the weekly override.

The quota bars keep provider remaining visible and mark reserved quota with hatching. At 54% used with an 80% cap, the provider has 46% remaining: 20% is reserved and 26% is usable by Codex LB.

When enabled, the limit is a hard routing gate for every strategy, including
sticky and single-account routing. Codex LB stops selecting the account once a
current standard quota window reports usage at or above the configured
percentage. It also stops selecting the account when current usage data is
missing or stale (fail-closed) until a fresh observation restores eligibility.
The account's upstream status is not changed.

You can disable a configured limit without forgetting its percentage, or remove
it to clear the value. Because upstream usage is observed after requests finish,
delayed reporting and requests already in flight can move actual usage past the
displayed limit before the gate sees it.

The cap also applies to new turns on existing HTTP/WebSocket connections and to
public, limit-reset, and quota-planner warmups. A continuation that requires a
capped owner is rejected rather than silently moved to another account. An
unavailable or deleted owner is reported separately from a reached cap.

An enabled `100%` limit is **not** the same as disabling the feature: it still
requires current telemetry and blocks at 100%. Missing telemetry is not treated
as a real zero-percent measurement in usage history or demand calculations.

After a save, the account list refreshes authoritative data. Older outstanding
reads cannot revert the acknowledged policy, and overlapping policy edits are
applied in order. Fresh owner checks read committed configuration directly;
ordinary selection on another replica may briefly retain cached inputs until
invalidation or cache expiry. Work already dispatched is not cancelled.

`account_usage_limit_reached` means the local policy blocks the account (because
the cap is reached or current telemetry is unavailable).
`account_usage_limit_authorization_failed` means the local authorization read
could not be completed; retry after the database/service recovers. It is not an
upstream HTTP response.

---

*Specs: [account-routing](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/account-routing) · [frontend-architecture](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/frontend-architecture) · [usage-refresh-policy](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/usage-refresh-policy)*

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
payloads also bypass the bridge. Bypassing the bridge does not force upstream
HTTP: an `input_image` request keeps upstream HTTP only when its payload exceeds
the WebSocket frame budget or still carries an external image URL, and otherwise
follows the ordinary transport precedence. Source-routed Chat requests keep
their source.

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
