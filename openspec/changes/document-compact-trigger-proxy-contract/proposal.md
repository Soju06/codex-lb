## Why

PR 1749 changes two coupled parts of the Codex compact contract: trigger
canonicalization and the upstream transport used to obtain the compact result.
The upstream Codex Responses flow accepts a terminal `compaction_trigger` on
`POST /backend-api/codex/responses`; the legacy `/codex/responses/compact`
route can return 404. The proxy therefore needs an explicit transport contract,
including the retained `/v1` compatibility behavior, rather than relying on an
implementation detail that contradicts the existing context notes.

## What Changes

- Document that `POST /backend-api/codex/responses` terminal compaction
  triggers produce exactly one terminal `compaction` item on the internal
  compact wire.
- Document that malformed top-level trigger placement is rejected locally
  before upstream compact handling.
- Document that Codex compact transport uses streamed `POST
  /backend-api/codex/responses` with `stream=true` and `store=false`, and
  reconstructs the compact response from the terminal SSE lifecycle.
- Document that legacy message-shaped compact output is converted to a
  `compaction` item while only valid opaque `cmp_` IDs are preserved; malformed
  IDs are omitted rather than rewritten.
- Document that the standalone Codex `/backend-api/codex/responses/compact`
  route remains a compatibility endpoint, while `/v1/responses/compact`
  preserves duplicate-trigger normalization for existing OpenAI-compatible
  clients.

## Impact

- `responses-api-compat` change record only
- The existing compact transport guidance that requires direct
  `/codex/responses/compact` without a surrogate is superseded for the Codex
  Responses bridge by this explicit streamed `/codex/responses` contract.
