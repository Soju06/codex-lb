## Why

The Accounts page currently has two separate Export buttons (Codex format and OpenCode auth) with divergent UX flows. The Codex export auto-downloads without preview, while the OpenCode export shows a modal with token previews and copy functionality. Users must know which format they need before clicking. A single unified export with in-modal format switching provides a simpler surface and consistent security-aware UX.

## What Changes

- **Single "Export" button** on AccountActions replaces the current two buttons
- **Single combined backend endpoint** `POST /api/accounts/{id}/export/auth` returns structured tokens plus both Codex and OpenCode authJson objects in one response
- **Unified `AuthExportDialog`** modal (title: "Auth Export") with a mode dropdown selector ("codex" / "opencode", default "codex") that switches the displayed authJson and token previews instantly without re-fetching
- **Codex mode**: shows truncated `id_token`, `access_token`, `refresh_token` with per-token Copy buttons, and the codex-format `auth.json` block with Copy + Download
- **OpenCode mode**: unchanged modal behavior (truncated `access_token`, `refresh_token` with Copy, opencode-format `auth.json` block with Copy + Download)
- **Two old endpoints** `POST /api/accounts/{id}/export` and `POST /api/accounts/{id}/export/opencode-auth` are **deprecated** — kept for backward compatibility, no frontend consumers
- **Two old mutations** `exportMutation` and `exportOpenCodeAuthMutation` in `use-accounts.ts` replaced by a single `exportAuthMutation`
- **Old `OpenCodeAuthExportDialog`** component renamed/rewritten as `AuthExportDialog`

## Capabilities

### New Capabilities

- `unified-auth-export`: Single combined endpoint returning structured tokens plus both Codex and OpenCode authJson formats; frontend modal with format-mode selector

### Modified Capabilities

- `frontend-architecture`: The Accounts page export surface changes from two buttons to one; modal interaction model changes to include a mode dropdown

## Impact

- **Backend**: New `POST /api/accounts/{id}/export/auth` endpoint in `app/modules/accounts/api.py`; combined service method in `app/modules/accounts/service.py`; audit event `account_auth_exported`
- **Frontend**: `AuthExportDialog` replaces `OpenCodeAuthExportDialog`; `AccountActions` collapses to single export button; `use-accounts.ts` gains `exportAuthMutation` replacing two mutations; `api.ts` gains `exportAccountAuth`; `schemas.ts` gains new response schema; `account-detail.tsx` and `accounts-page.tsx` wiring simplified
- **Deprecation**: Old `POST /export` and `POST /export/opencode-auth` endpoints remain but are no longer called from the frontend
- **Tests**: New dialog tests for mode switching; updated integration tests for combined endpoint; updated hook/component tests
