## Context

Durable bridge state records both the last owner account and latest upstream response ID. When a hard session returns after its in-memory upstream WebSocket is gone, codex-lb currently injects that response ID into a fresh upstream request. If the client sent full history, codex-lb also trims the verified stored prefix and sends only the suffix.

Production evidence shows the ChatGPT upstream can silently ignore that stale response anchor on a new WebSocket. This reproduced both when a request recovered locally after its prior workload owner disappeared and after replacement workloads were fully ready. The existing 240-second eventless watchdog eventually fails closed, but OpenCode often cancels and retries earlier, recreating the same session-scoped failure. Forking succeeds because the fork sends full context without the durable anchor.

## Goals / Non-Goals

**Goals:**

- Avoid stale-anchor submission when a fresh bridge already has verified complete context.
- Preserve the durable owner account and hard-affinity identity.
- Preserve anchor injection for incremental follow-ups that cannot stand alone.
- Keep post-send ambiguity fail-closed.

**Non-Goals:**

- Move a hard-continuity request to another account.
- Replay a request after an eventless timeout or uncertain upstream acceptance.
- Treat arbitrary client history as complete without the existing count-and-fingerprint proof.
- Change watchdog timing or account-health policy.

## Decisions

### 1. Prefer verified context only for a fresh upstream bridge

The no-anchor path applies only when durable lookup found no live local session and no active remote owner, the incoming request has no explicit `previous_response_id` or conversation handle, and its full-resend prefix matches the durable input count and fingerprint. The durable lookup remains active for owner-account routing.

### 2. Do not seed the new session with the stale response ID

The newly created bridge starts a fresh upstream conversation from the complete client payload. It must not copy the old durable response ID into the in-memory session before submission, because session-level anchor injection would recreate the same stale request. A successful response establishes the new bridge's normal response anchor.

### 3. Preserve incremental recovery

If the request is not a verified full resend, the durable response ID remains the only available context representation. Existing anchor injection and fail-closed handling stay unchanged.

## Risks / Trade-offs

- The full resend can be larger than the trimmed anchored request, but it is used only during fresh reattach and is the same complete payload the client already supplied.
- A malformed or incomplete resend could lose context, so eligibility continues to require the existing exact durable prefix proof.
- The old anchor may still be valid, but avoiding it on a fresh connection removes an unnecessary ephemeral dependency while retaining the owner account.

## Example

A durable row records 83 input items and `resp_old`. A returning client sends those 83 items plus four new items. With no live bridge owner, codex-lb verifies the 83-item prefix, opens a new bridge on the recorded account, and sends all 87 items without `previous_response_id`. A client that sends only the four new items still receives `resp_old` as its reattach anchor.
