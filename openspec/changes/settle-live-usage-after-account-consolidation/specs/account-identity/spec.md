## ADDED Requirements

### Requirement: Duplicate consolidation preserves a recoverable canonical identity

Identity reconciliation MUST preserve the upstream ChatGPT account id on the canonical row, reparent existing account-owned usage history to that row, and remove selected duplicate rows when it consolidates duplicate local accounts under the existing email and workspace-slot policy. Reconciliation MUST NOT consolidate distinct real-email account slots solely to make an upstream identity unique.

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
