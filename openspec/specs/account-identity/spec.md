# account-identity Specification

## Purpose
Defines how codex-lb identifies a stored account when upstream identifiers are not unique. Several workspace members can share one upstream `chatgpt_account_id`, so keying account slots on that identifier alone let one member's login or reauthorization overwrite another member's tokens. This capability keeps each login email on its own slot, makes duplicate consolidation preserve a recoverable canonical identity, and keeps dashboard OAuth polls from acting on stale generations.
## Requirements
### Requirement: Shared upstream workspace identities preserve account slots

The account import and OAuth add-account flows MUST preserve separate local account slots for different real email addresses even when the upstream token reports the same ChatGPT account id, with or without a workspace id.

Dashboard account summaries MUST expose and render the upstream ChatGPT account id as the primary workspace/account-slot context before falling back to optional workspace metadata or a generic unknown-workspace label.

#### Scenario: Shared workspace account ids preserve separate emails
- **GIVEN** two account credentials have different real email addresses
- **AND** both credentials report the same upstream ChatGPT account id
- **WHEN** the operator imports or adds both accounts through OAuth
- **THEN** the system persists separate local account slots for each email
- **AND** the second account does not overwrite the first account's stored email or tokens

#### Scenario: Workspace context uses ChatGPT account id
- **GIVEN** an account has a ChatGPT account id
- **WHEN** the dashboard renders the account workspace context
- **THEN** it displays the ChatGPT account id
- **AND** it does not display the generic unknown-workspace label

### Requirement: Duplicate consolidation preserves a recoverable canonical identity

Identity reconciliation MUST preserve the upstream ChatGPT account id on the canonical row, reparent existing account-owned usage history to that row, and remove selected duplicate rows when it consolidates duplicate local accounts under the existing email and workspace-slot policy. Reconciliation MUST NOT consolidate distinct real-email account slots solely to make an upstream identity unique. On PostgreSQL, every account insertion, replacement, token/metadata identity update, duplicate consolidation, and deletion that changes upstream-identity membership MUST acquire the same transaction-scoped upstream-identity advisory lock as live-usage settlement before row or fold-state locks and hold it through commit. An old-to-new membership move MUST acquire both stable identity lock keys in canonical sorted order.

#### Scenario: Same-slot duplicate leaves one upstream-resolvable canonical row

- **GIVEN** canonical account `C` and duplicate account `D` are selected for consolidation by the existing identity policy
- **AND** both rows carry the same upstream ChatGPT account id
- **WHEN** reconciliation consolidates `D` into `C`
- **THEN** `C` remains with that upstream ChatGPT account id
- **AND** existing usage history formerly owned by `D` is owned by `C`
- **AND** `D` no longer exists
- **AND** the upstream ChatGPT account id resolves uniquely to `C`

#### Scenario: Shared-workspace sibling slots remain distinct

- **GIVEN** two current accounts have different real email addresses
- **AND** they share the same upstream ChatGPT account id
- **WHEN** identity reconciliation evaluates the accounts
- **THEN** it preserves both local account slots
- **AND** it does not consolidate either account solely to make upstream resolution unique

### Requirement: Dashboard OAuth polls ignore stale generations

The dashboard OAuth client MUST isolate in-flight status polls and start continuations by a monotonic generation that reset and restart invalidate. A poll MUST capture the flow ID and completion credentials before awaiting status or completion. After each of those awaits, the client MUST continue only when the generation is still current and the captured flow ID still identifies the live flow. After awaiting OAuth start, the client MUST apply the new flow only when that start's generation is still current. A fenced poll or start MUST NOT complete OAuth, MUST NOT write success or error onto a newer flow, and MUST NOT invalidate account or dashboard caches. An uninterrupted current-flow poll MUST still apply success, error, and pending results as it does today.

#### Scenario: Stale successful poll after reset and restart is ignored

- **GIVEN** dashboard OAuth flow A is pending and a status poll for A is awaiting
- **AND** the operator resets and starts flow B
- **WHEN** the in-flight poll for A later resolves as success
- **THEN** the client does not call OAuth completion for A or B
- **AND** it does not mark flow B success or error
- **AND** it does not invalidate account or dashboard caches
- **AND** flow B remains the live pending flow

#### Scenario: Stale error poll after reset and restart is ignored

- **GIVEN** dashboard OAuth flow A is pending and a status poll for A is awaiting
- **AND** the operator resets and starts flow B
- **WHEN** the in-flight poll for A later resolves as error
- **THEN** the client does not write A's error onto flow B
- **AND** flow B remains the live pending flow

#### Scenario: Stale start success after reset and restart is ignored

- **GIVEN** dashboard OAuth start A is awaiting
- **AND** the operator resets and starts flow B
- **WHEN** start A later resolves as success
- **THEN** the client does not replace flow B with A's credentials
- **AND** flow B remains the live pending flow

#### Scenario: Stale start error after reset and restart is ignored

- **GIVEN** dashboard OAuth start A is awaiting
- **AND** the operator resets and starts flow B
- **WHEN** start A later fails
- **THEN** the client does not write A's error onto flow B
- **AND** flow B remains the live pending flow

#### Scenario: Current-flow poll success still completes

- **GIVEN** dashboard OAuth flow A is pending and no reset or restart has occurred
- **WHEN** a status poll for A resolves as success and completion succeeds
- **THEN** the client completes flow A
- **AND** it marks the live flow success
- **AND** it invalidates account and dashboard caches

