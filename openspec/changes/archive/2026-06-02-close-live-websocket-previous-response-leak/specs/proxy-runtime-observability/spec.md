## ADDED Requirements

### Requirement: Runtime continuity canary reports raw-error exposure and build parity
Operators MUST have a local verifier that reports whether the running `codex-lb` runtime is built from the expected code and whether recent Codex client logs contain raw `previous_response_not_found` errors.

#### Scenario: live runtime is checked after a continuity patch
- **WHEN** an operator runs the verifier on the Mac host
- **THEN** the verifier reports the repo commit, the running container image/id, local `/health` status, and recent raw `previous_response_not_found` count
- **AND** the verifier exits nonzero if raw errors are still present after the verification window
- **AND** the verifier redacts response ids by default unless `--show-ids` is passed

### Requirement: Request-log persistence failures are operator-visible
If request-log persistence fails for Responses WebSocket requests, the runtime MUST surface that condition in logs or verifier output so operators do not mistake HTTP `/health` success for continuity safety.

#### Scenario: request-log persistence fails during WebSocket traffic
- **WHEN** the runtime logs a request-log persistence failure
- **THEN** the verifier reports the failure count
- **AND** the continuity closeout cannot be marked green until persistence failures are absent or explicitly explained
