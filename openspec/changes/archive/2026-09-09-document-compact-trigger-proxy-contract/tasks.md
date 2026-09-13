## 1. Document the contract

- [x] 1.1 Add a focused `responses-api-compat` delta for the compact-trigger
  proxy-routing contract.
- [x] 1.2 Cover exactly one terminal compact-wire `compaction_trigger` and
  local malformed-placement rejection.
- [x] 1.3 Record the streamed `/backend-api/codex/responses` compact transport,
  the standalone Codex compatibility endpoint, and the `/v1` normalization
  asymmetry.
- [x] 1.4 Record the legacy message-shaped compact ID filtering contract.

## 2. Validate the change

- [x] 2.1 Run `openspec validate document-compact-trigger-proxy-contract --strict`.
