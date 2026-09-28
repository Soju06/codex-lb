## ADDED Requirements

### Requirement: Windows route failures classify with POSIX parity

The service MUST classify a typed Windows error as a host-route failure only
when it is the Windows form of a host-route errno that is already classified on
POSIX. Typed winerror 1231 (`ERROR_NETWORK_UNREACHABLE`, the `ENETUNREACH`
form) and 1232 (`ERROR_HOST_UNREACHABLE`, the `EHOSTUNREACH` form) MUST be
classified as host-route failures. Windows errors that report a peer reset,
timeout, refusal, or abort, including 64 (`ERROR_NETNAME_DELETED`) and 121
(`ERROR_SEM_TIMEOUT`), MUST remain account and endpoint attributed, as
`ECONNRESET`, `ETIMEDOUT`, `ECONNREFUSED`, and `ECONNABORTED` are. Windows
classification MUST come from the typed `winerror` field, not message text, and
MUST NOT by itself prove that dispatch did not occur.

#### Scenario: Ambiguous Windows route failure retires transport without replay

- **WHEN** an HTTP operation raises a typed OSError with winerror 1231 or 1232
- **AND** no connector provenance proves that dispatch did not begin
- **THEN** the failed shared generation is eligible for retirement
- **AND** the selected account's health remains unchanged
- **AND** the failed request is not automatically replayed

#### Scenario: Windows route connector failure can retry safely

- **WHEN** a typed connector failure contains an OSError with winerror 1231 or 1232
- **THEN** recovery MAY retry the request on the same account within its deadline

#### Scenario: Windows reset or timeout stays account attributed

- **WHEN** an HTTP operation raises a typed OSError with winerror 64 or 121
- **THEN** the failure is recorded against the selected account's circuit breaker
- **AND** the shared outbound HTTP client is not rotated
- **AND** the failure is not reported as `proxy_network_unavailable`

#### Scenario: Windows message text does not establish provenance

- **WHEN** an exception message contains `[WinError 1231]` or `[WinError 1232]`
- **AND** its typed winerror field is absent
- **THEN** it does not enter process-network recovery
