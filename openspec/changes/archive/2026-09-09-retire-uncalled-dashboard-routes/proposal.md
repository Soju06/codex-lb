## Why

The 0908 slop-removal audit (`03-frontend.md` A.4, `01-dead-python.md` §8 item 4)
found 13 of the 99 `/api` dashboard routes have no frontend caller. Four of them
are also either explicitly deprecated in code or dashboard-only by design with no
other consumer, so they are dead surface that still costs handler, service,
schema, mock and test code and keeps two `deprecated=True` entries in the
OpenAPI document indefinitely. The owner delegated the retirement decision with
one rule: remove a route only if it is (a) marked deprecated in code/spec/docs,
or (b) dashboard-only by design — no docs page, no OpenSpec requirement naming
it as a public API, no CLI/script/Helm/test-fixture consumer, and not part of
the fleet or API-key-holder surfaces.

## Consumer audit

Each of the 13 uncalled routes was grepped across `docs/`, `openspec/specs/`,
active `openspec/changes/`, `frontend/src/`, `scripts/`, `deploy/`, `.github/`
and `tests/`.

| Route | Evidence | Decision |
|---|---|---|
| `POST /api/accounts/{id}/export` | `deprecated=True` since `2026-07-12-unify-auth-export`; superseded by `POST /export/auth`; only refs are backend tests and an MSW mock | **remove** (rule a) |
| `POST /api/accounts/{id}/export/opencode-auth` | `deprecated=True`, same change; `/export/auth` returns the identical `opencodeAuthJson` payload | **remove** (rule a) |
| `DELETE /api/sticky-sessions/{kind}/{key}` | frontend `deleteStickySession` client removed in #2183; UI deletes single rows through `POST /delete` with a one-item batch; no docs/spec path reference | **remove** (rule b) |
| `GET /api/conversation-archive/files` | frontend only calls `/records`; `ConversationArchiveFileSchema` had no consumer; `proxy-runtime-observability` describes record lookup by request id, not file listing; only refs are auth-gate probes in tests | **remove** (rule b) |
| `GET /api/request-logs/{conversation_id}` | **false positive** — the handler is on `conversations_router` (`/api/conversations/{id}`), documented in `docs/conversations.md`, governed by `conversations-api`, and called by `features/dashboard/api.ts` | keep |
| `GET /api/fleet/summary`, `/observability`, `POST /refresh` | Bearer-API-key operator surface specified in `fleet-summary` | keep |
| `GET /api/usage/summary`, `/history`, `/window` | no docs/spec path reference, integration-tested (`test_usage_api.py`) | keep — follow-up decision |
| `GET /api/audit-logs` | no docs/spec path reference, integration-tested (`test_audit_logs_api.py`) | keep — follow-up decision |

## What Changes

- Delete the two deprecated account export handlers, their `AccountExportResponse`
  / `AccountOpenCodeAuthExportResponse` models and the `export_account` /
  `export_opencode_auth` service methods. `POST /export/auth` remains the single
  export endpoint and already returns both payload formats.
- Delete `DELETE /api/sticky-sessions/{kind}/{key}`, `StickySessionDeleteResponse`
  and `StickySessionsService.delete_entry`. Single-row deletion is served by
  `POST /api/sticky-sessions/delete` with a one-entry batch; the repository
  `delete()` stays because proxy sticky selection uses it.
- Delete `GET /api/conversation-archive/files`, `ConversationArchiveFileResponse`,
  `ConversationArchiveFile`, `list_archive_files()` and `_date_from_filename()`.
  Record lookup keeps resolving archive paths internally.
- Move backend tests to the surviving routes (unified export, batch delete,
  `/records` auth gate) and fold the OpenCode export scenarios into
  `test_account_auth_export.py`; drop the MSW mock and coverage entry for the
  retired export route and the dead frontend archive-file schema.

## Capabilities

### Modified Capabilities

- `account-auth-export`: the OpenCode-format export is delivered through the
  unified export endpoint; there is no dedicated OpenCode export route.
- `sticky-session-operations`: single-mapping deletion is expressed through the
  batch delete endpoint; the per-item DELETE route no longer exists.
- `unified-auth-export`: the two deprecated predecessor routes are retired and
  respond as unknown routes.

## Impact

`app/modules/accounts/{api,schemas,service}.py`,
`app/modules/sticky_sessions/{api,schemas,service}.py`,
`app/modules/conversation_archive/{api,schemas,service}.py`, their tests, and
the frontend MSW mocks/schemas. No documented public route changes: `docs/`
never referenced any retired path. Clients that still call a retired route get
the unmatched-route response (`404`, or `405` where the SPA catch-all partially
matches the path); no payload is served.
