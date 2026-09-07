## Context

See [proposal.md](proposal.md). The SPA currently wraps all routes in `AuthGate`, and the shared application layout mounts header/status components that issue administrator dashboard requests. The backend already has `validate_usage_api_key`, which always requires an active, unexpired Bearer API key regardless of the global proxy-auth switch, and `/v1/usage` already returns self-scoped lifetime totals.

## Goals / Non-Goals

**Goals:**

- Establish a separate authentication boundary for API key users.
- Reuse the existing strict API-key validator and `/v1/usage` summary contract.
- Enforce request-log ownership server-side and redact sensitive fields by response-schema construction.
- Reuse the visual language of the dashboard request grid without mounting administrator data consumers.

**Non-Goals:**

- Granting API key users a dashboard session or access to administrator APIs.
- Exposing account pool identities, API key metadata, conversation data, client data, or routing topology.
- Persisting the supplied credential, adding a core navigation item, or changing `/apis`.

## Decisions

### Put `/key-dashboard` outside `AuthGate`

The top-level router will render a standalone lazy `KeyDashboardPage` for `/key-dashboard`; all other routes remain under `AuthGate` and `AppLayout`. The standalone page does not mount `AppHeader` or `StatusBar`, preventing incidental administrator API calls.

### Authenticate with an in-memory Bearer credential

The input uses a masked field. After submission, the credential is cleared from the DOM and retained only in a React ref for refresh/pagination calls. It is excluded from URL state, TanStack query keys, storage, logs, and error text. Requests use `credentials: "omit"` and suppress the global dashboard-session 401 handler so password cookies and API-key failures cannot affect one another.

### Fetch summary and logs in parallel

A page-owned loader runs `/v1/usage` and `/api/key-dashboard/request-logs` with `Promise.all`. This reuses the established self-service summary and avoids a sequential authentication/data waterfall. It deliberately does not put the raw credential or key-dashboard results in the shared query cache; an opaque in-memory request generation discards results from stale reconnects.

### Add a privacy-by-construction log DTO

The new backend endpoint forces `api_key_ids=[authenticated_key.id]`; clients cannot submit an API key selector. Its Pydantic response model contains only the fields used by the safe grid. Sensitive fields are not copied into the wire DTO and cannot appear as nullable accidental payload fields.

### Reuse the request table with a fixed safe column set

The frontend adapts the narrow DTO to the established request-table view model and enables only Time, Model, Transport, Status, TTFT, TPS, Tokens, Cost, and Details. Every sensitive view-model field is explicitly set to null, while a dedicated table option disables administrator-only detail rendering.

## Risks / Trade-offs

- [A stolen API key can read its own usage] → This is the intended possession-based access model; TLS remains required in deployment, the key is never persisted by the page, and revoked/expired keys fail immediately.
- [Shared table code may gain new sensitive fields later] → The backend DTO is allowlisted, the frontend adapter nulls sensitive fields, the visible column list is fixed, and tests assert forbidden JSON keys and headings are absent.
- [Lifetime summary refresh repeats during pagination] → The two small requests remain parallel; this avoids additional credential/session state and keeps the implementation zero-config.

## Migration Plan

Ship the new API route and lazy frontend chunk together. No data migration or setting is required. Rollback removes the standalone route and endpoint without affecting `/dashboard`, `/apis`, proxy authentication, or stored API keys.
