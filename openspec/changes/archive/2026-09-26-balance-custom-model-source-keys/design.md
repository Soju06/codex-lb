## Context

Source lookup orders by name/ID and returns one row. Responses already uses an owned dispatch with admission before reservation and cancellation-safe finalization. Model sources each contain one credential; aliases map only after source selection. Source request logs persist response ID and source ID at finish. Existing model_source_pins primitives are reserved for subscription overflow and remain inert here.

## Goals / Non-Goals

**Goals:** Use five credentials declared in five otherwise equivalent sources for Codex Responses without client changes, while preserving settlement and continuity.

**Non-goals:** Multi-key schema, clone UI, global HA capacity coordination, source WebSockets, subscription overflow changes, or retrying an already-returned stream. Other protocol routes keep their current policy.

## Decisions

- Membership lookup stays independent of transient health, so busy/cooling custom models never fall through to subscription accounts. Reload eligible candidates on retries using the exact public model and presenting key's source scope.
- Choose the least in-flight source; break equal-load ties by least recent selection with randomized ties for independently starting replicas. The existing synchronous bulkhead claim is acquired before any reservation await. Bound in-memory state to 10,000 entries, reset it when source configuration changes, and keep it per replica like current bulkheads.
- For pools, temporarily cool credential failures (300 seconds), 429 (60 seconds) and gateway/server/connect failures (5 seconds). Honor numeric or HTTP-date Retry-After within 1–600 seconds. Single-source requests retain existing behavior. No sleeps or background tasks are introduced.
- Retry at most five distinct sources and only after explicit credential/rate/server rejection or a transport failure proven to occur while connecting. Header/first-frame/total timeouts and malformed successful bodies may already have executed work and are not automatically replayed. Stop once any stream is returned, for anchored requests, client disconnects or quota-finalization failure. Preserve the last actual upstream error if attempts exhaust.
- Keep per-attempt SourceDispatch ownership; cleanup and log settlement complete before cooldown mutation or the next reservation. A failed reservation release inhibits failover.
- Resolve source anchors from request logs with exact API-key and model scope. Known owners must still satisfy current source assignment, enablement and capability; otherwise fail closed. Conflicting/unknown owners in a pool fail closed instead of rotating. A missing owner for one candidate retains prior single-source compatibility. Subscription ownership is checked first and file/compaction exclusions remain intact.
- Logs are shared across HA replicas but written only on dispatch finish and subject to retention. A follow-up arriving before the owner row or after retention may be declined; callers can retry after completion or resend full context without the anchor. Never guess among multiple sources.

## Risks / Trade-offs

- Local counters/cooldowns mean aggregate concurrency can reach the per-source limit times replica count; independent ties reduce synchronized first-choice bias but are not global balancing.
- HTTP errors can carry provider-specific semantics. Preserve redacted error bodies and Retry-After; never classify client validation errors, mid-stream errors or client cancellation as safe replay.
- Same model across sources promises equivalent capabilities to clients; operators should copy aliases, capabilities and agent metadata consistently.
- Request-log failure/retention loses positive ownership evidence. Reject ambiguous continuation rather than leak an anchor to another credential.
