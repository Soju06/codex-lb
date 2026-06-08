## Context

The Accounts page currently has two export buttons wired to two separate API endpoints with divergent UX flows:
- **Codex Export** (`POST /export`): Returns authJson as a raw string. Frontend auto-downloads a blob immediately—no preview, no copy, no security warning.
- **OpenCode Auth Export** (`POST /export/opencode-auth`): Returns structured data with individual tokens. Frontend opens a modal with truncated token previews, copy buttons, and a Download button.

Both endpoints decrypt tokens at request time and set no-cache headers. Both log audit events. The code is duplicated across backend service methods and frontend hooks/mutations.

## Goals / Non-Goals

**Goals:**
- Single "Export" button replacing two buttons in AccountActions
- Single combined backend endpoint returning all data in one request
- Modal with mode dropdown ("codex" / "opencode", default "codex") that switches display instantly
- Codex mode shows truncated id_token, access_token, refresh_token with Copy; codex-format auth.json block with Copy + Download
- OpenCode mode keeps existing token preview and auth.json behavior unchanged
- Old endpoints deprecated but retained for backward compatibility

**Non-Goals:**
- Changing the underlying auth.json formats (codex legacy, OpenCode stock) themselves
- Removing the old endpoints from the codebase (they stay, just unreferenced by frontend)
- Adding import/export for other formats
- Changing the OAuth import flow

## Decisions

### D1: Combined endpoint returns structured tokens + both authJson formats

**Chosen:** `POST /api/accounts/{id}/export/auth` returns structured tokens (`id_token`, `access_token`, `refresh_token`, `expires_at_ms`) alongside both `codexAuthJson` and `opencodeAuthJson` as structured objects.

**Rationale:** The modal needs individual token values for truncated display and Copy buttons, regardless of which mode is selected. Building both authJson formats server-side keeps the frontend stateless and avoids duplicating format construction logic. A single request means no re-fetch when switching modes.

**Alternative considered:** Two separate endpoints + frontend state machine — rejected because it adds latency on mode switch and forces two API calls for data that comes from the same decrypted tokens.

### D2: Existing modal component extended rather than rewritten from scratch

**Chosen:** Rename `OpenCodeAuthExportDialog` to `AuthExportDialog`, add dropdown selector, conditionally render codex token rows.

**Rationale:** The existing dialog already has the security warning, token preview layout, copy buttons, and download. Adding a dropdown and conditional rendering is minimal change with low regression risk.

### D3: Old endpoints marked deprecated, kept for backward compat

**Chosen:** Keep `POST /export` and `POST /export/opencode-auth` in the backend and API client, but remove all frontend consumption (button, hook, page wiring).

**Rationale:** If any external scripts or tools call these endpoints, they continue to work. Deprecation can happen later in a separate cleanup PR.

## Risks / Trade-offs

- **Response size**: The combined endpoint returns both authJson formats (~2x payload). Token payloads are small (~few KB). Negligible impact.
- **Backend decryption**: Tokens are decrypted once per call, same as current behavior. No additional security risk.
- **Modal complexity**: Adding a dropdown adds one more interactive element. Low risk given the existing dialog is already well-tested.

## Migration Plan

1. Add new backend endpoint + service method + schema
2. Add new frontend schema, API function, mutation
3. Create `AuthExportDialog` component (derived from existing dialog)
4. Update `AccountActions` to single button
5. Update `AccountDetail` props (remove `onExportOpenCodeAuth`)
6. Update `AccountsPage` wiring (single mutation + single dialog)
7. Write/update tests
8. Deploy — no breaking changes, old endpoints still available
