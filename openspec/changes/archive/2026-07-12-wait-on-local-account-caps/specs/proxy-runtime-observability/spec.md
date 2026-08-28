## MODIFIED Requirements

### Requirement: Streaming timeout diagnostics are emitted

For `/v1/responses` HTTP/SSE streams, the service MUST log low-cardinality diagnostics for early heartbeat emission, keepalive emission, account-capacity recovery waits, startup wait timeout, downstream disconnect, and stream idle timeout. The diagnostics MUST include request id, route family, account id when known, timeout or wait stage, model when known, bounded sleep or elapsed seconds where available, and normalized error code/message where available, without exposing payload content, API keys, raw affinity keys, or raw account emails.

#### Scenario: Keepalive path is diagnosable

- **WHEN** a streaming Responses request waits for upstream events long enough to emit keepalive data
- **THEN** the service records heartbeat or keepalive diagnostics
- **AND** the diagnostic does not include raw prompt-cache keys or request payloads

#### Scenario: Account-capacity recovery wait is diagnosable

- **WHEN** a streaming Responses request waits because account selection returned a recoverable capacity or rate-limit retry hint
- **THEN** the service logs the request id, route family, model when known, bounded wait seconds, recovery hint seconds, and normalized selection error
- **AND** the diagnostic does not include account emails, API keys, raw affinity keys, prompt text, or request payload content

#### Scenario: Local account cap wait is diagnosable

- **WHEN** a streaming request waits because local per-account stream or response-create capacity is exhausted
- **THEN** downstream keepalive and logs use the account-capacity wait path with normalized local cap reason fields
- **AND** the diagnostic does not expose prompt content, API keys, raw affinity keys, or account emails
