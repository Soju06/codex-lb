## Context

The existing pre-visible unary policy permits retry on another account after a confirmed transient connect failure. Routed file operations wrap `CodexTransportError` in `FileProxyError`, dropping dispatch provenance; their sanitized messages also omit the text patterns used by the legacy classifier.

## Goals / Non-Goals

**Goals:** Carry structured retry eligibility into the existing unary retry loop and verify the actual files API route.

**Non-Goals:** New retry loops, changes to account selection, owner pinning, API-key reservation ownership, network recovery policy, or retrying requests with ambiguous delivery.

## Decisions

Preserve typed transport phase and replay eligibility in the files adapter, using the existing `ProxyResponseError` transport metadata at the service boundary. Typed transport failures must be classified from their replay flag, not by reinterpreting sanitized message text. Preserve process-network error codes so host-wide failures do not become account-local failover. Keep legacy direct transport classification unchanged.

Use the existing `_retry_previsible_unary_call_failover` loop, which excludes the failed account, observes the deadline and refuses strict owner changes. For example, a proxy connection refusal for a new file upload can select account B after account A; finalization of a file owned by A remains on A and fails closed.

## Risks / Trade-offs

- Accidentally replaying an upload after dispatch → negative tests for body-read and ambiguous failures.
- TLS/configuration errors gaining retries through a transient-looking endpoint name → typed provenance overrides text heuristics.
- Changing ownership or replaying process-wide network failures across accounts → pinned-finalize and process-network negative controls.
- Shared unary classifier changes affect other endpoints → legacy-classification and typed-transport tests plus existing unary regression coverage.
