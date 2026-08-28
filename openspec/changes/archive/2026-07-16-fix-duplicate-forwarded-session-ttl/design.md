## Context

`HTTPConnection.headers` is a Starlette `Headers` object that preserves repeated raw fields. `Headers.get(name)` returns one field value, while `Headers.getlist(name)` exposes every occurrence. The long-session loopback-host-header override currently calls `get()` for each forwarded client-IP header, so an empty first field hides a later non-empty value and bypasses the 12-hour cap.

The original hardening in PR #1137 intentionally allows long sessions only for direct local requests or an explicitly enabled localhost-published bridge path without a forwarded client identity. This fix preserves that trust boundary and the existing treatment of absent or entirely empty fields.

## Goals / Non-Goals

**Goals:**

- Treat any non-empty value among all occurrences of all supported forwarded client-IP headers as evidence that disables the long-session override.
- Preserve the configured long lifetime when those headers are absent or every occurrence is empty.
- Prove the duplicate-field ordering case with the same raw ASGI header representation used at runtime.

**Non-Goals:**

- Change the 30-day threshold, 12-hour cap, dashboard auth modes, or persisted settings.
- Reinterpret trusted proxy chains or alter general request-locality resolution.
- Add a generic header abstraction or dependency for one security predicate.

## Decisions

### Inspect every value through `Headers.getlist()`

Iterate the existing forwarded client-IP header names and every value returned by `request.headers.getlist(name)`. This matches Starlette's runtime representation and closes ordering-dependent behavior without copying or reparsing headers.

Alternative: reject every repeated field. Rejected because repeated `Forwarded` and `X-Forwarded-For` fields are legal and the TTL predicate only needs to know whether any client-identity hint is non-empty.

Alternative: add a shared public helper to `request_locality`. Rejected for this independent fix because the TTL predicate is small, the current upstream module has no suitable public helper, and a new cross-module API would widen the patch.

### Preserve all-empty compatibility

An absent header and one or more empty values continue to mean that no forwarded client identity was supplied. This matches the original `any(value)` intent and avoids broadening the compatibility change beyond the proven bypass.

### Test the raw duplicate ordering

Construct the `Request` with two `X-Forwarded-For` fields: an empty first occurrence and a non-empty remote second occurrence. Assert that the effective lifetime is 43,200 seconds.

## Risks / Trade-offs

- A deployment that intentionally sends a non-empty forwarded client-IP field while relying on the loopback-host-header override will now receive the documented 12-hour cap. This is the security boundary, not a regression.
- The fix depends on Starlette's `Headers.getlist()` API, already used elsewhere in the project and available on every `HTTPConnection.headers` object.
- Whitespace-only values remain truthy, as before for a first field. Treating them as a client-identity hint is fail-closed.
