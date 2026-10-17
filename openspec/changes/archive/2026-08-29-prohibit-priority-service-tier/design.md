## Context

`prohibitFastMode` is currently evaluated inside model-alias normalization. That location can only decline to derive `service_tier: "priority"` from a `fast` alias; it cannot remove a tier supplied directly by a client, introduced by API-key enforcement, or already present on a forwarded payload. The policy therefore disagrees across HTTP, compact, WebSocket, warmup, and owner-forwarding paths.

Outbound Responses payloads are mutable typed request models. Request preparation already converges on `apply_api_key_enforcement` for ordinary HTTP, compact, chat-conversion, and WebSocket traffic, while warmup and owner-forward replay have narrow preparation paths of their own. Routing and quota reservation inspect `payload.service_tier` before serialization, so wire-only filtering would leave internal selection and accounting inconsistent with what is sent.

## Goals / Non-Goals

**Goals:**

- Enforce one canonical definition of prohibited priority service tiers across every outbound Responses path.
- Remove the tier before model-source selection, quota reservation, request logging, and upstream serialization.
- Preserve API-key enforcement provenance and all non-priority wire behavior.
- Make every removal observable through a low-cardinality info log.

**Non-Goals:**

- Changing priority-tier pricing, response-tier accounting, or upstream response interpretation.
- Changing model alias, reasoning-effort, WebSocket continuity, or source-routing behavior beyond the tier prohibition.
- Adding a Prometheus series solely for this bug fix; the removal log gives direct evidence without expanding the metrics contract, while existing service-tier metrics continue to describe effective requests.

## Decisions

### Decision: Centralize the policy in one typed request-policy helper

Add one helper in `request_policy.py` that inspects a `ResponsesRequest` or `ResponsesCompactRequest`, canonicalizes its current tier with `canonical_service_tier_value`, and sets only canonical priority values to `None` when prohibition is enabled. It logs the request ID and original stripped value.

The helper runs after the last tier writer in each preparation flow. `apply_api_key_enforcement` invokes it after alias normalization and API-key tier enforcement; warmup invokes it after alias normalization; owner-forward preparation invokes it after restoring the signed effective tier. This keeps routing, reservations, logs, and serialization aligned while retaining one implementation of the rule.

Alternatives considered:

- Filtering only in `to_payload()` was rejected because model-source selection and quota reservation happen before serialization and would still observe priority.
- Adding independent string checks at every route was rejected because it duplicates policy and risks drift.
- Retaining the alias-specific prohibition plus adding explicit-tier filtering was rejected because it leaves two enforcement mechanisms with different precedence and observability.

### Decision: Global prohibition overrides API-key enforced priority

An administrator-enabled global prohibition wins over a key's `enforced_service_tier`. API-key enforcement still runs and returns its existing provenance, but the shared prohibition helper removes a resulting canonical priority tier before downstream decisions.

This precedence matches an operator-wide safety control: a narrower credential policy must not bypass it. When the global setting is disabled, API-key behavior is unchanged.

### Decision: Omit prohibited priority tiers

Set the typed field to `None`, producing no `service_tier` key upstream. Do not substitute literal `"default"`: existing code documents that the subscription backend rejects some literal default-tier values, and absence is already the established wire representation for the upstream default. Non-priority values and an already absent field remain untouched.

### Decision: Preserve connection-snapshot semantics for WebSockets

The WebSocket path continues using the `prohibitFastMode` value captured when the connection begins. Each `response.create` frame is normalized with that snapshot, including explicit priority fields. Changing the dashboard setting still requires reconnecting an existing WebSocket to observe the new policy.

## Risks / Trade-offs

- [A forwarded request could restore a prohibited signed tier after ordinary enforcement] → Invoke the shared helper after the forwarded tier restoration, immediately before downstream routing and serialization.
- [Refactoring alias normalization could change disabled-policy behavior] → Add red-first tests for enabled and disabled alias, explicit, and API-key cases; priority remains derived normally when prohibition is off.
- [Clearing the tier could leave quota/accounting metadata stale] → Enforce before reservation and request-state capture, and assert effective outbound/request-state tiers in path-level tests.
- [Lower-frequency request paths may bypass ordinary API-key enforcement] → Enumerate every assignment, mapping normalization, and outbound serialization path, and cover warmup plus forwarded preparation explicitly.

## Migration Plan

No data migration or configuration change is required. Deploying the code makes an already-enabled setting effective for all new HTTP requests and newly connected WebSockets. Rollback restores the prior alias-only behavior.

## Open Questions

None.
