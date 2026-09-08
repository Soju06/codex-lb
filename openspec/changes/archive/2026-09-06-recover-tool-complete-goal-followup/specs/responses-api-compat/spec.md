## ADDED Requirements

### Requirement: Exact tool-manifest replay preserves fresh user follow-up

When a fingerprint-verified durable input prefix is followed by every call and
matching output in the durable prior-response pending-tool manifest, the
full-resend context proof SHALL permit trailing self-contained user input after
that complete batch. The proof MUST require exact call IDs and call types,
complete settlement, no duplicate or orphan result, and no call-ID collision
with the stored prefix. It MUST reject user input interleaved with the batch or
any later call, output, or instruction-role message after the fresh-input suffix
begins. The existing bounded developer-interleave exception MUST NOT gain a
trailing-input extension.

Account switching MUST still require the complete projected request to pass
account-neutral replay classification, the durable prefix to match, and the
existing pre-dispatch recovery and account-scope checks. A qualifying replay
MUST retain the complete calls, outputs, and fresh input. This proof MUST NOT
authorize transferring encrypted compaction, account-owned files, conversation
references, or an unverified input prefix.

#### Scenario: Goal follow-up after completed tool batch can leave exhausted owner

- **GIVEN** the input contains a verified stored prefix and exactly settles its durable pending-tool manifest
- **AND** a self-contained goal-continuation user message follows the complete tool batch
- **WHEN** the previous owner is unavailable before dispatch and the complete projected body is account-neutral
- **THEN** the bridge can use its existing fresh replay path on another eligible account
- **AND** it retains the calls, results, and goal instruction without the old response anchor

#### Scenario: Fresh input does not conceal a missing parallel result

- **GIVEN** a durable manifest contains multiple calls
- **WHEN** a resend appends user input after only part of the recorded batch
- **THEN** context proof fails and cross-account replay remains forbidden

#### Scenario: New input does not relax developer-interleave bounds

- **GIVEN** a suffix uses the existing three-item custom-call/developer/output exception
- **WHEN** additional user input follows that suffix
- **THEN** this exception remains ineligible for exact-manifest proof

