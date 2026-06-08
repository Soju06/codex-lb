## 1. Source of truth
- [x] Create this OpenSpec change as the active source of truth for Codex-native WebSocket long-wait continuity.
- [x] Audit existing previous-response / WebSocket continuity changes and mark which requirements are superseded or retained here.
- [x] Re-check official Codex client behavior and reclassify proxy ledger as fallback, not primary fix.
- [x] Sync stable context back to `openspec/specs/responses-api-compat/context.md` after implementation is verified.

## 2. Tests first
- [ ] Add/port a Codex client regression where a long-wait tool-output continuation receives stale-anchor continuity failure and the same turn retries once as full `response.create` without `previous_response_id`.
- [x] Add a direct `/backend-api/codex/responses` WebSocket regression proving codex-lb returns a sanitized/classifiable stale-anchor error and never leaks raw `previous_response_not_found` or the missing response id.
- [ ] Add a regression proving stale-anchor auto-retry is attempted at most once per failed sampling request.
- [ ] Add a regression proving non-stale-anchor errors still surface normally and do not trigger full-context retry.

## 3. Implementation
- [ ] Prefer Codex client soft-reset retry: detect stale-anchor continuity errors, clear incremental WebSocket session state, rebuild from conversation history, and retry once without `previous_response_id` before emitting `EventMsg::Error`.
- [x] In codex-lb, preserve direct WebSocket masking and add a stable sanitized error classifier if `stream_incomplete` is too generic for the client.
- [x] Keep proxy-side local replay ledger work out of the primary path unless the client cannot be patched; if needed, implement it as a narrow compatibility fallback.
- [x] Keep direct WebSocket route logic centralized and covered by OpenSpec before adding more branch-local previous-response patches.

## 4. Verification
- [ ] Run focused Codex client WebSocket continuity tests if the client/fork is in scope.
- [x] Run codex-lb direct WebSocket previous-response masking/classification tests.
- [x] Run OpenSpec validation with `openspec validate --specs`.
- [x] Produce a short branch/SoT cleanup note listing superseded older changes/branches and the canonical change id.
