## ADDED Requirements

### Requirement: Gated model selection failures expose stable proxy error codes
When account selection fails for an explicitly mapped gated model, the proxy MUST return a stable OpenAI-format error code that distinguishes plan support failures, stale additional-quota data, and zero eligible accounts.

#### Scenario: Missing fresh additional quota data returns a specific code
- **WHEN** a compact or streaming Responses request targets a mapped gated model and the latest persisted additional-usage snapshot for its canonical `limit_name` is unavailable or stale
- **THEN** the proxy returns an OpenAI-format error envelope with a stable code for unavailable additional quota data

#### Scenario: No eligible accounts returns a specific code
- **WHEN** a compact or streaming Responses request targets a mapped gated model and the canonical `limit_name` has fresh persisted data but no eligible accounts
- **THEN** the proxy returns an OpenAI-format error envelope with a stable code for zero eligible additional-quota accounts
