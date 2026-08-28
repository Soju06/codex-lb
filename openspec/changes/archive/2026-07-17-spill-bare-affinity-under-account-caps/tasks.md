## 1. Affinity and owner policy

- [x] 1.1 Classify bare process-session affinity with a source-separated opaque selection key while preserving raw hard turn-state keys.
- [x] 1.2 Make previous-response, file, conversation, live/durable bridge, and replay ownership override bare-session locality at the shared selection boundary.

## 2. Request-local account-cap spillover

- [x] 2.1 Allow the load balancer to filter a bare-session owner through account stream/response-create caps and select an eligible alternate.
- [x] 2.2 Keep the stored soft mapping unchanged on spillover, retain it when below cap, and preserve stable local-cap failure when no alternate exists.
- [x] 2.3 Propagate only the selection-time spillover capability through Responses, compact, direct WebSocket, and HTTP bridge callers without transport settlement, rollback, publication, or replay state.

## 3. Regression coverage

- [x] 3.1 Cover affinity-source classification, source-key collision isolation, raw legacy fail-closed behavior, and owner-bearing payload revocation.
- [x] 3.2 Cover non-mutating spillover, unsaturated owner retention, no-alternate preservation, hard-owner precedence, and ambiguous conversation failure.
- [x] 3.3 Cover externally visible HTTP/compact selection and verify late WebSocket/bridge capacity paths do not switch shared or durable transport ownership.

## 4. Verification and documentation

- [x] 4.1 Add production comments at source-strength, owner-precedence, non-persistence, and transport-handoff boundaries.
- [x] 4.2 Run focused and affected tests, lint, formatting, typing, architecture, simplicity, and strict OpenSpec validation.
- [x] 4.3 Sync the delta specs and stable rationale to the main capability docs, verify the change, and archive it before PR creation.
