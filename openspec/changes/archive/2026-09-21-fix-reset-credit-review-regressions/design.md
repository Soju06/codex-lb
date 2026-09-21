## Context

Review R1–R8 invalidated the earlier local readiness conclusion. The existing ledger and per-account serializer provide the basis for recovery; changes must preserve exact-credit selection and task/session ownership.

## Goals / Non-Goals

Goals: fix all eight findings with product-path regression coverage and an independent re-review. Non-goals: new settings, deployment, changing upstream guarantees, or claiming that every burst fits every network timeout budget.

## Decisions

- Queue detached account data at the scheduler boundary, before publishing each restored row or closing the consuming session, including partial restore failure. Every verification loads current credentials through its own session.
- Persist already-received receipts in an owned, cancellation-protected task with bounded retries and fresh sessions. Only persistence is retried; a confirmed consume is never repeated to repair a database write.
- Apply credit-level receipt reconciliation to manual selection under the existing account serializer. A terminal credit conflicts with a new request; new client IDs for an unresolved credit are durably pinned to that same credit and resolve to its oldest request, which owns upstream attempts and receipts. This preserves recovery after a browser reload without letting an alias retry target a later credit. Legacy no-body callers also recover the selected credit's unresolved request.
- Cap reconciled availability by both the authoritative count and the remaining item evidence; never resurrect authoritative zero.
- Use a targeted duplicate-identity lookup with the same normalization as full-list mapping.
- Calculate dashboard aggregate changes using sample eligibility. Preserve pending state across server query replacement through query reconciliation, with query generations guarding in-flight stale responses. Snapshot timestamps guard reset fields only; fresh server health, policy and usage remain authoritative.
- Share an expiry-aware retry calculation between persisted outcomes and the scheduler, honoring only future persisted retry timestamps and reserving time for another attempt and preventing a rapid retry loop near expiry.

## Risks / Trade-offs

Alias pins are marked with origin `alias`; timestamp ordering alone cannot choose the upstream owner under clock skew. All pins and the canonical outcome for a credit remain live while any pin for that credit is within the 24-hour retention window. Purge and read eligibility use the same group rule, so an alias cannot outlive the receipt it references. This extends canonical retention when a later alias is accepted, without changing the upstream request identity.

- Prolonged database outage or process death can still prevent storing a received receipt. Bounded finalization must report failure honestly and never fabricate confirmation.
- Upstream latency can exceed remaining credit lifetime. Tests establish scheduling and bounded fan-out, not an unconditional throughput guarantee.
- Account-list and dashboard polling can race targeted reads. Regression tests include pending and stale poll responses.

## Migration Plan

No additional schema migration is required. Validate locally and re-review before any separately requested deployment.
