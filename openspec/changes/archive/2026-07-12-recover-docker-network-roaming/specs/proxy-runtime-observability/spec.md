## ADDED Requirements

### Requirement: Process-wide network recovery is observable without sensitive resolver data

The service MUST emit low-cardinality structured diagnostics when it detects a process-wide DNS or route failure, rotates shared transport state, retries a safe request, recovers, or exhausts the request budget. Diagnostics MUST NOT contain DNS server addresses, request payloads, API keys, access tokens, raw continuity keys, or account email addresses.

#### Scenario: Recovery diagnostics are emitted

- **WHEN** a safe Responses request enters and later exits process-wide network recovery
- **THEN** logs identify the recovery stage, request id, transport, attempt count, and internal account id when known
- **AND** logs do not expose resolver configuration or request content

#### Scenario: Concurrent rotation is coalesced visibly

- **WHEN** several callers report a network failure from the same shared client generation
- **THEN** diagnostics distinguish the caller that rotated the client from callers that reused the already-rotated replacement
