# API Key Dashboard Context

## Purpose

`/key-dashboard` is a self-service surface for an API key holder, not an alternate administrator dashboard. It lets a key holder inspect only that key's lifetime usage and recent request activity without receiving a dashboard password or learning anything about the backing account pool.

## Decisions

- The route sits outside `AuthGate` and the administrator layout because those components initiate password-session, status, settings, and other operator-only requests.
- The existing `/v1/usage` endpoint remains the source for lifetime totals. A separate read-only endpoint supplies recent log rows because the administrator request-log response contains account, API key, client, source, routing, and failure metadata.
- A dedicated profile endpoint maps the already-validated API key through an explicit allowlist. It exposes display identity, lifecycle dates, and effective policy values without exposing the key ID, hash, assignments, usage-section configuration, or pooled account data.
- Configured limit consumption remains sourced from `/v1/usage`, avoiding a duplicate limit representation in the profile contract.
- The recent-log service derives the key ID from the validated Bearer credential and passes that ID directly to the repository filter. There is no client-controlled key selector.
- The response is constructed from a dedicated allowlist DTO. Redaction is not implemented by serializing the administrator DTO with selected values set to null, because field names alone reveal the operator data model and future fields could leak by default.
- The browser keeps the entered secret in component memory by default. An unchecked “remember on this browser” option may persist it in a namespaced local-storage entry only after a successful complete load; invalid authentication and Disconnect remove the entry. Requests omit cookies and bypass the global dashboard-session 401 handler so invalid keys cannot start or invalidate administrator auth flows.

## Constraints and failure modes

- API key authentication is mandatory for this surface even when proxy API key authentication is globally optional.
- A missing, unknown, inactive, or expired key receives an independent 401 response.
- If any initial request fails, partial results are not rendered. A 401 clears the in-memory and remembered credential and returns the user to key entry.
- Browser-local persistence is convenient but readable by scripts running in the same origin, so it is explicit and disabled by default rather than automatic.
- Soft-deleted logs and logs belonging to other keys are excluded by the server-side repository query.
- Account, plan, raw API key and database identity, client fingerprint, conversation/archive identity, model-source identity, upstream-proxy routing identity, and free-form failure details are intentionally unavailable on this surface. Only the key's display name and masked prefix are exposed as self-identification metadata.

## Example

A user opens `/key-dashboard`, optionally enables “remember on this browser,” enters `sk-clb-…`, and the browser concurrently requests `/api/key-dashboard/profile`, `/v1/usage`, and `/api/key-dashboard/request-logs?limit=25&offset=0` with that value in the Bearer header. After all three succeed, the page may show the key name and masked prefix, lifecycle and policy details, limit consumption, 42 requests, 12K total tokens, 3K cached tokens, $0.42 cost, and recent rows with model/status/token/latency values. It cannot show which account handled a row, the API key's database ID/hash, a client IP, or any upstream proxy route.
