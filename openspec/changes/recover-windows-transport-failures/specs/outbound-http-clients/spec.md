## ADDED Requirements

### Requirement: Typed Windows transport failures recover without unsafe replay

The service MUST classify local DNS resolver failures, host-route failures, and
typed Windows transport failures 64 and 121 separately from account-specific
upstream failures. Classification MUST come from typed exception provenance or
an already-preserved stable internal code, not from arbitrary message text. A
Windows transport classification MUST NOT by itself prove that dispatch did not
occur. Only typed pre-dispatch connection failures MAY be replayed automatically.
When such a failure affects the current shared outbound HTTP client, subsequent
callers MUST use a replacement client while active leases remain valid.

#### Scenario: Ambiguous Windows failure retires transport without replay

- **WHEN** an HTTP operation raises a typed OSError with winerror 64 or 121
- **AND** no connector provenance proves that dispatch did not begin
- **THEN** the failed shared generation is eligible for retirement
- **AND** the selected account's health remains unchanged
- **AND** the failed request is not automatically replayed

#### Scenario: Windows connector failure can retry safely

- **WHEN** a typed connector failure contains an OSError with winerror 64 or 121
- **THEN** recovery MAY retry the request on the same account within its deadline

#### Scenario: Windows message text does not establish provenance

- **WHEN** an exception message contains `[WinError 121]` or `[WinError 64]`
- **AND** its typed winerror field is absent
- **THEN** it does not enter Windows transport recovery
