## Purpose

Expose explicit subscription-term metadata from stored ChatGPT credentials without confusing it with token expiry or quota reset timing.

## Requirements

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

### Requirement: Compact recorded plan duration in account selectors

The original Detail selector MUST show recorded plan time immediately beneath each account status badge. List rows MUST also show a compact duration. Positive durations MUST use zero-padded whole days and remaining whole hours with literal d/h suffixes, such as `18d 08h`; positive periods below an hour MUST display `00d 00h`. Unknown deadlines MUST show an unavailable label, and elapsed deadlines MUST show a distinct elapsed label without changing status. Compact displays MUST expose recorded deadline, last check when available and recorded-period interpretation through their title or accessible description, while the selected detail retains full visible metadata. They MUST use the shared page clock and update at least each minute without issuing per-account requests.

#### Scenario: Duration beneath the active badge
- **WHEN** the Detail selector has an Active account with 18 days and 8 hours remaining
- **THEN** `18d 08h` appears immediately below Active
- **AND** account selection still updates the original inline statistics and charts

#### Scenario: Compact duration rolls over an hour boundary
- **WHEN** a displayed positive period falls below one hour
- **THEN** the compact display shows `00d 00h` until its deadline
- **AND** at the deadline it changes to an elapsed label rather than a negative duration

#### Scenario: Missing metadata has no inferred duration
- **WHEN** subscription metadata is unavailable but access-token expiry is known
- **THEN** the compact display shows an unavailable label without using token expiry
