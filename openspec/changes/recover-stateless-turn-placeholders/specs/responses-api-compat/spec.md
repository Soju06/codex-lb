## ADDED Requirements

### Requirement: Stateless requests retain synthetic turn-state placeholder compatibility

For HTTP and direct WebSocket Responses requests, an unregistered proxy-shaped turn-state marker without a previous-response anchor MUST NOT by itself establish an owner-miss continuation when the complete original request is proven account-neutral and self-contained. Such a request MUST use normal eligible account selection, including when multiple accounts are configured, and MUST preserve its complete input and controls without projection or dropping fields.

The proof MUST reject opaque reasoning or compaction state, unresolved tool outputs, account-owned references, unsupported metadata and unknown fields. Registered marker ownership, previous-response ownership, durable bridge ownership, file pins and non-synthetic turn-state constraints MUST remain authoritative. Requests that do not satisfy the stateless proof MUST retain the existing fail-closed or sole-candidate continuation rule. Lookup failures and conflicting owners MUST NOT be treated as unregistered placeholders.

#### Scenario: First request echoes an unregistered synthetic placeholder

- **GIVEN** multiple subscription accounts and no registered owner for a proxy-shaped turn-state marker
- **WHEN** a native HTTP or direct WebSocket request has no previous response and contains a complete stateless request
- **THEN** normal account selection can dispatch the unchanged request
- **AND** the marker alone does not cause `previous_response_owner_unavailable`

#### Scenario: Complete tool transcript remains intact

- **WHEN** the same request contains a complete self-contained tool-call and output transcript
- **THEN** it can proceed only if the whole original request satisfies the existing account-neutral replay proof
- **AND** every input item is forwarded in order

#### Scenario: Opaque or incomplete continuation remains bound

- **WHEN** an unregistered marker accompanies opaque state, an unresolved tool output or unknown metadata
- **THEN** the request does not receive stateless-placeholder recovery
- **AND** multiple possible owners still cause the existing sanitized continuity error

#### Scenario: Known ownership still wins

- **WHEN** a registered marker, previous response, durable bridge or file identifies an owner
- **THEN** that owner remains binding regardless of whether the request body is stateless
