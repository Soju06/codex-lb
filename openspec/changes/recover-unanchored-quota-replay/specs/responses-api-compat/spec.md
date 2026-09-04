## ADDED Requirements

### Requirement: Unanchored full resends recover from pre-visible quota rejection

The proxy MUST permit account failover for a Responses streaming request with no
previous-response or conversation anchor, no turn-state or input-file owner, and no
single-account routing after the selected account rejects the request for quota or rate
limits before any downstream event only when it can construct an account-neutral full resend.

The replay input MUST be produced by the shared response-owned-bookkeeping projection. The
projected request MUST pass the shared account-neutral fresh-replay validation and MUST retain
completed assistant output followed by fresh user input or an exact Codex host-generated
scheduled-task heartbeat. The proxy MUST preserve the requested
model, reasoning configuration, instructions, tools, and other account-neutral controls. It
MUST clear the failed attempt's soft payload-owner marker, exclude the rejected account, and
reallocate advisory prompt-cache affinity before reselection.

The proxy MUST NOT cross accounts for a request carrying a nonblank previous-response or
conversation anchor, a turn-state owner, an input-file owner, single-account routing, an
incomplete or non-neutral transcript, or any downstream-visible output. A non-quota failure
MUST retain its existing retry and ownership behavior.

#### Scenario: Full local transcript survives an exhausted sticky account

- **GIVEN** account A is selected for an unanchored prompt-cache-affine request
- **AND** the input contains a full self-contained transcript, response-owned reasoning state,
  retained assistant output, and fresh user input or a canonical scheduled-task heartbeat
- **AND** account B is eligible
- **WHEN** account A returns a quota rejection before any downstream event
- **THEN** the proxy removes response-owned reasoning state and item ids from the replay
- **AND** the proxy sends the account-neutral full resend on account B
- **AND** the response from account B is returned successfully

#### Scenario: HTTP and SSE quota rejection use the same recovery

- **WHEN** the pre-visible quota rejection arrives as either an HTTP error status or the first
  `response.failed` SSE event
- **THEN** the same account-neutral failover rules apply

#### Scenario: Scheduled heartbeat survives an exhausted sticky account

- **GIVEN** an unanchored full resend contains Codex host-generated scheduled-task heartbeats
- **AND** each heartbeat has the canonical `codex_app` automation shape and no upstream call id
- **WHEN** the selected account returns a pre-visible quota rejection
- **THEN** the proxy treats historical heartbeats as account-neutral host input
- **AND** the current heartbeat is retained as fresh input on the replay to another account
- **AND** malformed, namespaced differently, or call-id-bearing function outputs remain fail-closed

#### Scenario: Delta-shaped owner state stays fail-closed

- **GIVEN** an unanchored request whose input contains response-owned state and fresh user
  input but no retained prior assistant output
- **WHEN** the selected account returns a pre-visible quota rejection
- **THEN** the proxy surfaces the quota failure
- **AND** no part of the request is sent to another account

#### Scenario: Hard ownership stays fail-closed

- **GIVEN** a request carrying a previous-response, conversation, turn-state, or input-file
  owner, or constrained by single-account routing
- **WHEN** the owner returns a pre-visible quota rejection
- **THEN** the existing owner-bound behavior remains in force
- **AND** the proxy does not use this recovery to cross accounts
