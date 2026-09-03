## Context

See `proposal.md` for motivation. The key dashboard currently loads totals from `/v1/usage` and redacted rows from `/api/key-dashboard/request-logs`; the raw key otherwise exists only in component memory. API key validation already resolves a typed `ApiKeyData` value containing safe display metadata alongside sensitive identifiers and assignments, so serialization must remain explicitly allowlisted.

The dashboard reuses the administrator request table, whose default column sizing was designed for additional Account, Plan, and API Key columns. The usage summary reuses a generic stat-card component whose accent is controlled per stat.

## Goals / Non-Goals

**Goals:**

- Add key details without introducing another database lookup or exposing internal ownership/routing fields.
- Make remembered credentials an explicit, reversible browser-local choice.
- Improve summary scanability and make the reduced request-grid layout use available width coherently.
- Keep initial dashboard data atomic so partial profile, usage, or log data is not rendered.

**Non-Goals:**

- Editing API key settings from the self-service dashboard.
- Revealing backing accounts, model sources, credit pools, or administrator-only request data.
- Encrypting a browser-stored key with application-managed material; without a separate user secret that would not protect against script execution in the origin.
- Changing administrator table widths or global stat-card defaults.

## Decisions

### Add a dedicated profile endpoint

`GET /api/key-dashboard/profile` will reuse mandatory `validate_usage_api_key` authentication and pass its `ApiKeyData` result through a service-layer allowlist mapper. This avoids a client-selectable identifier and avoids a redundant query. Returning the operator API-key DTO was rejected because it contains assignments, pooled credits, and other fields outside the self-service privacy boundary.

### Load profile, usage, and logs concurrently as one page result

The client loader will validate three strict schemas in parallel and commit state only after all resolve. Refresh repeats the full snapshot, while pagination may use the same loader for consistency. Independent partial rendering was rejected because it can show stale profile data beside current usage.

### Persist only after opt-in and successful authentication

An unchecked “remember on this browser” control governs a namespaced local-storage entry. The submitted key remains in memory while authentication is pending and is stored only after the complete initial load succeeds. On mount, a remembered key is loaded automatically; a 401 or Disconnect removes it. Automatic storage without consent was rejected because API keys are bearer credentials and local storage is readable by scripts in the same origin.

### Scope visual changes to the key dashboard

The shared request table will accept optional column-width overrides so the key dashboard can provide widths for its reduced allowlist without changing the administrator dashboard. Summary cards will receive distinct existing theme-compatible colors through their per-stat accent input rather than adding a new visual component.

### Type and render key limits from `/v1/usage`

The client will replace the unknown limit collection with a strict limit schema and render configured maximum, current, remaining, model filter, window, and reset time. The profile endpoint will not duplicate limit data, preserving `/v1/usage` as the source of consumption state.

## Risks / Trade-offs

- **[Remembered bearer token is exposed to same-origin script execution]** → Keep persistence opt-in and off by default, disclose it beside the control, maintain a narrow frontend dependency surface, and clear it on 401 or Disconnect.
- **[Profile last-used time can lag the current dashboard request]** → Present it as informational metadata and use the value resolved during authentication without another write/read cycle.
- **[Shared table extension could affect operator layout]** → Make width overrides optional and cover the key-dashboard configuration in frontend tests.
- **[A new profile field could leak in the future]** → Use strict backend and frontend allowlist schemas and assert exact response keys.

## Migration Plan

Deploy backend and rebuilt SPA together. The endpoint is additive and requires no database migration. Rollback removes the new endpoint/client rendering; any previously remembered entry remains namespaced and is ignored by the old client, then can be removed by clearing site storage.
