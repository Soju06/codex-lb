## 1. Regression Coverage

- [x] 1.1 Replace the unkeyed finalization-failure case with a limited-key integration that proves the completed image response transfers an unresolved reservation to tracked release ownership
- [x] 1.2 Add event-driven coverage for settlement cancellation and retrying release while persistence drain remains pending
- [x] 1.3 Cover generation and edit, streaming and non-streaming, to prove exactly one image settlement handoff and no internal Responses reservation owner

## 2. Tracked Image Settlement

- [x] 2.1 Add an image-facing API-key usage adapter that delegates captured image tokens to the existing tracked stream settlement lifecycle
- [x] 2.2 Route all four image completion paths through the adapter while preserving public response availability and public model attribution

## 3. Verification

- [x] 3.1 Run focused image and settlement tests, Ruff, type checking, proxy architecture checks, and strict affected OpenSpec validation
- [x] 3.2 Exercise the isolated HTTP image surface with a limited key, gated release retry, persistence drain, and real SQLite state assertions
- [x] 3.3 Verify implementation against this change, synchronize the delta, and archive the verified OpenSpec change
