## ADDED Requirements

### Requirement: Account summaries expose recorded subscription term

The account summary API MUST expose nullable `subscription.activeUntil` and `subscription.lastCheckedAt` as ISO 8601 timestamps, derived only from the stored ID token's `https://api.openai.com/auth` claims `chatgpt_subscription_active_until` and `chatgpt_subscription_last_checked`. Invalid or missing values MUST remain unknown without rejecting account identity or the account list. Naive upstream ISO datetimes MUST be interpreted as UTC. Free/unknown accounts and token/current-plan mismatches MUST NOT expose historical paid-term dates. JWT expiry, quota reset, and account import time MUST NOT serve as fallback subscription dates. Credentials MUST NOT be exposed.

#### Scenario: Existing paid credential contains a subscription deadline
- **WHEN** a stored Plus ID token has a valid explicit subscription deadline and matching current account plan
- **THEN** listing accounts returns that deadline and available last-checked timestamp without an upstream request or token re-import

#### Scenario: Subscription metadata is unavailable or malformed
- **WHEN** a token contains no subscription deadline or contains an invalid deadline
- **THEN** listing accounts succeeds with an unknown deadline and preserves other valid identity and token information
- **AND** it does not substitute token expiry or quota reset

#### Scenario: Paid metadata belongs to a previous plan
- **WHEN** an account is now free/unknown or its stored token plan differs from its current plan
- **THEN** the account summary does not report a remaining paid subscription term

### Requirement: Subscription display distinguishes recorded term from live status

Account grid cards and selected-account details MUST show time remaining until the recorded subscription deadline, its date, and the available last-checked date. Unknown metadata MUST display an explicit unavailable state. An elapsed deadline MUST be labeled as an elapsed recorded period and MUST NOT change account status or routing eligibility. The UI MUST distinguish subscription time from access-token expiry and quota reset. Remaining time MUST refresh at least once per minute while the page is mounted.

#### Scenario: Future deadline counts down
- **WHEN** the recorded deadline is in the future
- **THEN** the UI shows the remaining duration and recorded end date
- **AND** the countdown updates without a page reload

#### Scenario: Recorded period has elapsed
- **WHEN** the stored deadline passes
- **THEN** the UI shows an elapsed recorded period without a negative duration or assertion that the live subscription expired

#### Scenario: Missing subscription metadata
- **WHEN** the deadline is unknown
- **THEN** the UI explicitly indicates no subscription-term data even if the access token has a known expiry
