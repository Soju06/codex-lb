## ADDED Requirements

### Requirement: Credential-safe bridge fallback diagnostics
The service MUST log bounded exception category and numeric error metadata before an eligible pre-submit HTTP fallback, with request correlation and bridge-attempt duration. It MUST NOT log exception messages, request bodies, headers or tracebacks in this diagnostic. It MUST preserve routing, settlement and replay guards.

#### Scenario: Chained TLS failure
- **WHEN** a pre-submit connect failure qualifies for HTTP fallback
- **THEN** diagnostics retain known exception categories and numeric codes without raw message content and the existing fallback still completes

#### Scenario: Untrusted or cyclic exception metadata
- **WHEN** exception metadata includes strings or cycles
- **THEN** diagnostics exclude non-numeric codes, normalize unknown categories and stop within five causes
