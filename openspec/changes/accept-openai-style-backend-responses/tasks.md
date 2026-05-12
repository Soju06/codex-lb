## 1. Implementation

- [x] 1.1 Route `/backend-api/codex/responses` through OpenAI-compatible request normalization.
- [x] 1.2 Preserve existing SSE streaming, Codex session affinity, and HTTP bridge behavior.

## 2. Verification

- [x] 2.1 Add regression coverage for OpenAI SDK streaming against `/backend-api/codex`.
- [x] 2.2 Update backend Responses validation coverage for optional `instructions`.
- [x] 2.3 Run focused pytest and lint.
- [ ] 2.4 Validate OpenSpec once the OpenSpec CLI is available in the workspace.
