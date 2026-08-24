## ADDED Requirements

### Requirement: Verified self-contained full resends do not receive session-level previous-response anchors

The HTTP Responses bridge MUST NOT inject a retained session `previous_response_id` into a full resend whose matching stored prefix is followed by retained completed assistant output or by direct tool call/output items that exactly settle the session's pending call manifest. It MUST forward that verified self-contained input without session-anchor-based prefix trimming. A cumulative resend that omits required prior output or pending tool results MUST retain the existing session-anchor and trim behavior. This rule MUST NOT remove an explicit client-provided `previous_response_id`.

#### Scenario: Self-contained trimmable full resend remains unanchored

- **WHEN** a full resend matches the stored input prefix of a reusable bridge session and retains completed assistant output before fresh user input
- **THEN** the proxy sends the request without injecting that session response ID
- **AND** the proxy forwards the complete input instead of trimming the stored prefix

#### Scenario: Cumulative prompt without prior output remains anchored

- **WHEN** a matching cumulative prompt contains fresh user input but omits the prior assistant output
- **THEN** the proxy injects the compatible account-owned session response ID
- **AND** the proxy applies the existing stored-prefix trim behavior

#### Scenario: Complete pending tool loop remains unanchored

- **WHEN** a matching full resend exactly settles every direct tool call in the session's pending call manifest
- **THEN** the proxy sends the complete tool-loop resend without injecting the retained session response ID

#### Scenario: Omitted pending tool output remains anchored

- **WHEN** a matching full resend omits any direct tool output required by the session's pending call manifest
- **THEN** the proxy retains the compatible session response ID and existing interrupted-tool-output repair

#### Scenario: Explicit client anchor is preserved

- **WHEN** a client supplies its own `previous_response_id`
- **THEN** the full-resend session-anchor suppression does not remove or replace that client value
