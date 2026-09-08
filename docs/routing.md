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

Account pages display each window as **percent remaining**; the sticky reallocation thresholds in Settings are **percent used**. A `Sticky secondary threshold` of `70` means "move sticky sessions off an account once more than 70% of its secondary (weekly or monthly) window has been used" — in quota terms, once less than 30% remains. Note that routing evaluates thresholds against reported usage **plus temporary in-flight pressure** (concurrent requests and leased tokens), so reallocation can begin slightly before the raw account-page numbers reach the threshold.

### Prefer earlier reset

When enabled and several accounts are otherwise eligible, selection is restricted to the accounts whose selected quota window (5h or weekly) resets soonest. Weekly resets are compared in whole-day buckets; when the selected window has no known reset time, the other window is used as a fallback. The preference applies to the `Capacity weighted`, `Usage weighted`, and `Fill first` strategies; the fixed-order and draw-based strategies (`Round robin`, `Relative availability`, `Sequential drain`, `Reset drain`, `Single account`) ignore it.

### Limit warm-up

Limit warm-up sends **one small real request** (using the configured warm-up model and prompt) to an opted-in account when one of its quota windows is confirmed to have newly reset, verifying that the account responds. It consumes a small amount of quota. The optional staggered idle mode additionally pre-starts the 5h window of idle opted-in accounts before traffic arrives; the configured cooldown applies to these staggered idle probes, while ordinary reset-confirmed probes fire once per confirmed reset. Accounts opt in individually (`Enable warm-up` in account actions); the last attempt's result, model, and time are shown on the account list entry.

## Subscription-exhaustion overflow to a model source

> Shipping in stages. This release ships the designation control and its preflight so a source can be prepared; the overflow routing described below lands in a later release and is not active until then. Owning spec: [model-source-routing](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/model-source-routing); design: issue [#2123](https://github.com/Soju06/codex-lb/issues/2123), which supersedes the earlier proposals in #428 and #1664.

**Settings → Routing → "Overflow to model source when all subscription accounts are exhausted"** designates one OpenAI-compatible model source with Responses support as the place new requests go when the whole subscription pool is out of usage. It is **orthogonal to `Routing strategy`**: the strategy still decides which subscription account serves a request while any account can; overflow only answers the question "what happens when none can". Default **off**; the setting names an operator-owned paid source, so it can never be a default.

### Trigger

Overflow triggers only on **pool-wide usage exhaustion** — the exact condition that today returns `429 usage_limit_reached` with `resets_at` (every eligible account is `QUOTA_EXCEEDED` or `RATE_LIMITED` with used ≥ 100 % evidence). It never triggers on per-account capacity caps, API-key fair-share throttles, transient upstream errors, authentication failures, or a single account's rate limit: those keep their current answers.

### Eligibility

- New conversations, or requests without prior reasoning, on standard Responses models. The **gpt-5.6 family cannot overflow in this version** (its Responses-Lite / code-mode request bodies are not portable to an OpenAI-compatible source); the preflight marks those models.
- Unscoped API keys only. A key scoped to a set of sources already routes to them directly and never overflows; the preflight counts such keys.
- Background jobs never overflow.
- Declare the non-function tool types your source accepts (`custom`, `apply_patch`, `web_search`, `shell`, `tool_search`) on its model entries (`supports_search_tool`, `experimental_supported_tools` in the model's raw metadata). The preflight lists the undeclared types per model, together with missing vision, missing streaming, missing pricing (the cost tile shows $0) and a context window that is missing or smaller than the registry's (Codex compacts on the registry's arithmetic, so a smaller window fails every turn with a pre-stream 400).

### Stickiness and the drain window

A conversation that received source output is **pinned** to the source and stays there (7-day idle limit; compaction is unavailable on the source). Turning overflow **off** stops fresh overflow immediately and arms a drain deadline: conversations already on the source keep working for at most 7 more days, then expire. While that is running the dashboard shows the date by which every pinned conversation has expired (`subscriptionOverflowPinsExpireBy`, the switch-off time plus 7 days) — not the drain deadline itself (`subscriptionOverflowDrainUntil`, the switch-off time plus 29 days), which additionally spans the 21-day tombstone grace during which an expired conversation is still recognised as pinned and handled like one on a disabled source instead of being treated as new. Designating a source again clears both.

### Kill switches

| Action | Effect |
|---|---|
| Set the control to **Off** | No new overflow; pinned conversations drain for ≤ 7 days. |
| Disable the source (or the model on it) | Immediate. Reasoning-bearing pinned conversations end with an error; the others return to subscription accounts. |
| Delete the source | Clears the designation and arms the drain window in the same transaction; pinned conversations behave as for a disabled source. |

### Client behaviour

- WebSocket sessions cannot be served by a source. A pinned conversation arriving over WebSocket is answered with HTTP `426`, which makes Codex switch **that session** to HTTP until restart.
- Codex retry semantics for answers on this path: `429` is not retried (`retry_429: false`) and surfaces as "exceeded retry limit, last status: 429" — a source's own rate limit is therefore visible only as that message; `5xx` is retried (`retry_5xx: true`) through the usual reconnect ladder; `400 invalid_request_error` is rendered immediately.
- `/status` shows the subscription pool, not the source. Forked conversations may replay reasoning the source cannot read. While overflow is on, routing depends on the proxy database being reachable.
- Limited API keys stream live and are charged an estimate when the source omits usage or the client disconnects mid-answer; fast mode is served at standard tier on the source.

### Note on the bridge's usage-limit answer

Since the WP-E fix in #2124 the HTTP Responses session bridge returns the pool's `429 usage_limit_reached` immediately instead of waiting out the capacity window, so a client sees the same exhaustion answer on both the direct and the bridged path — the answer overflow later replaces.

---

*Specs: [account-routing](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/account-routing) · [frontend-architecture](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/frontend-architecture) · [model-source-routing](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/model-source-routing) · [usage-refresh-policy](https://github.com/Soju06/codex-lb/tree/main/openspec/specs/usage-refresh-policy)*
