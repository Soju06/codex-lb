## ADDED Requirements

### Requirement: Per-key assigned-account local usage share
An API key MAY configure `account_usage_percent` as an integer from 1 through 100. The dashboard API and API-key management UI SHALL create, update, and return this nullable value.

When an API key has account-assignment scope and this policy is configured, account selection SHALL exclude an assigned account when the key's successful, attributable request-log cost for that account in the account secondary quota window reaches the configured percentage of that account's local quota budget. The local quota budget MUST be projected into request-cost units from the account's `app.core.usage` secondary plan capacity, its live upstream used percentage, and the total successful local API-key request-log cost attributed to that account in the same window. The system MUST NOT compare request-log USD directly to plan-capacity credits or create an account-wide operator cap.

When the policy is unset, account/key attribution is unavailable, the account has no live secondary usage snapshot, the account has no known secondary capacity, no cost-to-credit observation is available, or local-usage aggregation cannot be read, selection SHALL preserve existing behavior. Request-log writes are asynchronous, so the ledger is eventually consistent across concurrent requests; it SHALL not claim cross-request atomic reservation semantics.
