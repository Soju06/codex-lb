## ADDED Requirements

### Requirement: Account-neutral replay proof may settle prefix-held direct tool calls

The account-neutral full resend proof MUST accept output-only suffixes that
settle pending direct tool calls from a verified stored prefix. The verified
stored prefix must contain the exact direct tool calls named by the durable
pending-tool manifest. The proof MUST reject suffix tool calls in that
prefix-settling mode. Each suffix output MUST have exactly one nonblank
`call_id`, MUST use the output type matching the manifest call type, MUST NOT
duplicate another suffix output call ID, and MUST NOT carry response-owned
fields outside the account-neutral tool-output field set.

#### Scenario: Suffix settles pending calls from the stored prefix

- **GIVEN** the verified stored prefix contains a supported direct tool call
- **AND** the durable pending-tool manifest names that call ID and call type
- **WHEN** the fresh suffix contains the matching direct tool output and no
  fresh tool call
- **THEN** account-neutral replay proof succeeds

#### Scenario: Malformed prefix-settling output fails closed

- **GIVEN** the verified stored prefix contains a supported direct tool call
- **WHEN** the fresh suffix contains an orphan output, duplicate output, missing
  call ID, mismatched output type, response-owned output field, or fresh tool
  call
- **THEN** account-neutral replay proof fails
