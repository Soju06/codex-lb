## Context

Trusted-header dashboard authentication trusts a configured identity field only from an allowlisted raw proxy peer. The middleware strips that field from untrusted peers but deliberately preserves every occurrence from trusted peers. The parser then uses singular Starlette header lookup, so repeated fields are reduced by order before the application establishes an admin actor.

## Goals / Non-Goals

**Goals:**

- Parse trusted identity cardinality before selecting an actor value.
- Reject every duplicate configuration-header occurrence, including equal and blank-plus-nonblank pairs.
- Preserve singleton authentication and the documented `proxy_auth_required` envelope for absent or invalid proxy identity.
- Prove behavior through the protected dashboard API boundary.

**Non-Goals:**

- Changing trusted proxy source classification or middleware ordering.
- Silently deduplicating values or splitting comma-delimited identity content.
- Changing password fallback behavior, roles, settings, or response schemas.

## Decisions

### Enforce cardinality in the trusted identity parser

Read all occurrences with Starlette's case-insensitive `getlist` interface and construct a trusted-header identity only when the list contains exactly one field whose trimmed value is non-empty. This is the narrow point where trusted transport evidence becomes an authenticated actor.

Rejecting in the sanitizer middleware was not selected because that middleware owns raw-peer provenance and scrubbing, not dashboard rejection semantics. Deduplicating equal values was rejected because repeated authentication evidence remains ambiguous and can hide a misconfigured append-style proxy.

### Reuse the existing missing-identity rejection path

The parser returns no trusted identity for duplicate fields. Protected dashboard dependencies then emit HTTP 401 with error code `proxy_auth_required`, exactly as they do for a missing or blank identity. No new public error type or configuration is needed.

## Risks / Trade-offs

- [Risk] A proxy that intentionally emits repeated equal identity fields will begin failing closed. → Document the singleton contract and preserve the existing response envelope so the misconfiguration is diagnosable.
- [Risk] A future parser bypass could reintroduce first-value selection. → Keep the regression at the protected route boundary, where an authenticated principal would be observable.
- [Trade-off] The response does not distinguish duplicate from missing identity. → Reusing the established envelope avoids exposing parser details and keeps this fix contract-compatible.
