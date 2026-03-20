# Proposal: extend HTTP responses bridge to backend Codex HTTP

## Why

Production measurements show the remaining cache weakness is concentrated in HTTP `/backend-api/codex/responses`, while websocket traffic and bridged `/v1/responses` HTTP already achieve much higher cache ratios.

## What Changes

- Route HTTP `/backend-api/codex/responses` through the existing shared upstream websocket bridge.
- Preserve Codex session affinity behavior and existing `/v1/responses` HTTP bridge behavior.
- Add backend-specific bridge regressions and update responses compatibility spec/context.

## Impact

- Better continuity and cache behavior parity for backend Codex HTTP callers.
- No new settings surface; reuse the existing HTTP responses bridge controls.
