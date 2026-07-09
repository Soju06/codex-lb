## MODIFIED Requirements

### Requirement: Streaming Responses requests use a bounded retry budget
When a streaming `/v1/responses` request encounters upstream instability, the proxy MUST enforce a configurable total request budget across selection, token refresh, account-capacity recovery waits, and upstream stream attempts. Each upstream stream attempt MUST clamp its connect timeout, idle timeout, and total request timeout to the remaining request budget.

#### Scenario: Local account cap selection waits instead of failing immediately
- **WHEN** account selection for a streaming Responses request fails locally with `account_stream_cap` or `account_response_create_cap`
- **THEN** the proxy treats the condition as a recoverable account-capacity wait within the request budget
- **AND** it retries account selection after the bounded wait instead of returning an immediate 429
- **AND** permanent `no_accounts` failures remain non-waitable unless they carry a distinct recoverable capacity or upstream quota signal
