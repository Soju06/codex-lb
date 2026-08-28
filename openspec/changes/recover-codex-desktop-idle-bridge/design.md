## Context

The HTTP Responses bridge serializes upstream `response.create` submissions with a per-session gate and releases that gate when `response.created` is observed. Current `main` can retire a stale pre-created gate owner when another request later times out waiting for the gate, but it has no owner-side deadline. A lone request can therefore remain pending indefinitely after a successful WebSocket send produces no matched `response.*` event.

The production request that motivated this change was eventless after its current `response.create` send and remained pending for 3,467 seconds. Codex Desktop disconnected first because its parsed-event idle timeout is 300 seconds. The backend route had classified the native request as OpenAI-SDK-shaped from its payload and `Accept` header, so periodic SSE comments never reached the parsed-event timer.

Current `main` already provides terminal request settlement, whole-session retirement, a stuck-retirement Prometheus counter, lifecycle locking, native Codex identity detection, and safe later-waiter recovery. This change reuses those primitives directly and does not import PR #1394's retry-circuit, replay, coordinator, or migration surface.

## Goals / Non-Goals

**Goals:**

- Terminate an eventless request that remains pre-`response.created` before the native client's 300-second idle boundary, without requiring another gate waiter.
- Measure the deadline from the current upstream send rather than request construction or admission wait.
- Fail closed through existing settlement and session-retirement paths without replaying ambiguous work or moving it to another account.
- Send parser-visible liveness to verified native Codex clients while retaining OpenAI event normalization when their payload needs it.
- Preserve structured low-cardinality retirement metrics and logs.

**Non-Goals:**

- Replay a pre-visible request, retry clean upstream closes, or persist retry cooldowns across replicas.
- Replay or move a request after any matched `response.*` lifecycle event. A
  missing-created stream that is still producing buffered response-lifecycle
  evidence stays on the same bridge and re-anchors the watchdog from that
  activity; downstream-visible output suppresses this narrow watchdog entirely.
- Change account selection, continuity ownership, request budgets, public `/v1/responses`, or operator settings.
- Merge PR #1394, deploy the result, or alter the current Mac mini runtime as part of this code change.

## Decisions

### 1. Measure from the actual current send

Add one optional monotonic `response_create_sent_at` field to the in-memory request state. Set it immediately before each actual upstream `send_text` of the current `response.create`. A later send replaces the timestamp, so admission and queue time from an earlier attempt cannot make a fresh send expire immediately.

This timestamp is protocol evidence, not a general request timer. Request start time is not an acceptable fallback for the proactive watchdog because a request may legitimately spend most of its budget waiting for admission before it reaches upstream.

### 2. Use the existing threshold with a client-safe cap

The proactive window is `min(http_responses_session_bridge_stuck_gate_retire_after_seconds, 240 seconds)`. This introduces no new setting, preserves deliberately shorter existing thresholds, and leaves at least 60 seconds before the native client's 300-second parsed-event timeout.

The upstream-reader wait uses the earliest applicable deadline. The watchdog remains active when SSE keepalives are disabled because downstream liveness and upstream acceptance are separate concerns.

### 3. Keep the eligibility deliberately narrow

The proactive timeout applies only while the current HTTP request:

- owns the response-create gate and is awaiting `response.created`;
- has a current send timestamp;
- has no response id or recorded `response.created` latency;
- has no downstream-visible output or sequence evidence.

Leading non-response telemetry such as `codex.rate_limits` does not change
those conditions. Matched response-lifecycle events before `response.created`
keep the missing-created watchdog armed but re-anchor it from the most recent
upstream response activity, so actively reasoning streams are not timed out from
the original send timestamp. Once output is forwarded downstream, the watchdog
is suppressed and existing stream/request-budget timeout behavior owns the
request.

### 4. Fail the whole bridge session closed

When the deadline expires, reuse the reader-owned terminal failure and whole-session retirement path. Emit a stable `missing_response_created_timeout` detail, increment the existing stuck-retirement metric, settle every pending request exactly once, and close the bridge session.

Do not transparently replay the timed-out request, submit it on another account, or mark the selected account unhealthy. Upstream acceptance is unknown, so duplicate submission and account movement are less safe than an explicit terminal failure. A later client request creates a fresh session through existing behavior.

Example: a request sends at monotonic time 1,000 with the default 300-second stuck threshold. With no matched response lifecycle event, it becomes eligible at 1,240 and receives an explicit terminal failure; it does not wait for a second request or the 300-second Desktop idle timeout.

### 5. Separate heartbeat identity from event normalization

Continue using `_is_openai_sdk_request` to decide whether response events need the OpenAI compatibility normalizer. Separately derive verified native identity from the existing `_is_native_codex_request` allowlist. A native request without explicit SDK fingerprint markers receives `CODEX_KEEPALIVE_FRAME` even if payload-shape heuristics enable normalization.

Explicit `x-stainless-*` headers or an OpenAI User-Agent retain comment liveness. Public `/v1/responses` never enables the native heartbeat override. This changes only liveness framing; it does not relax authentication, routing, payload validation, fingerprint normalization, or vendor-event filtering.

## Risks / Trade-offs

- **A send fails after the timestamp is set.** Existing send-error cleanup retires or settles the request before the watchdog can act; tests cover that the timestamp alone is not sufficient eligibility.
- **A quiet upstream accepted the request but emitted no event.** The proxy returns an explicit failure rather than risking a duplicate replay. The selected account remains healthy because silence is not proof of account failure.
- **A matched lifecycle event arrives just before timeout.** Eligibility is
  rechecked under the existing request/session synchronization before
  retirement, and response-lifecycle activity re-anchors the watchdog. If the
  event is forwarded downstream, downstream-visible evidence suppresses this
  narrow watchdog.
- **Whole-session retirement interrupts a healthy sibling.** This narrow design chooses fail-closed session cleanup rather than attempting unsafe sibling isolation on current `main`. Existing terminal settlement must cover every pending sibling exactly once.
- **A client spoofs native identity.** The only benefit is an ignored vendor liveness event on the authenticated Codex backend route; explicit SDK markers still take precedence.

## Migration Plan

No data migration or setting change is required. Deploying a new process initializes the monotonic field on new in-memory request states. Rollback restores the previous timeout and heartbeat selection without persistent-state conversion.

## Open Questions

None. The scope intentionally solves only the observed eventless/no-waiter production failure.
