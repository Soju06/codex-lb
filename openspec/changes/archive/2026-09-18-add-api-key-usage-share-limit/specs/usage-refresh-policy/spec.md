# usage-refresh-policy Delta

## ADDED Requirements

### Requirement: Usage-share admission fails open on incomplete upstream evidence

An API-key usage-share estimate SHALL require, for every account contributing capacity, a known normalized long-window capacity and a fresh current long-window sample containing usage percentage, a future reset deadline, and the canonical duration for that long-window slot whose derived window start is no later than the sample's recording time. If required evidence is missing, stale, elapsed, or otherwise incomplete, the API-key policy snapshot SHALL record the affected account ids instead of treating them as unused or denying the key.

When quota-consuming subscription work reaches usage-share admission with such a snapshot, Codex-LB SHALL admit it and request the existing debounced, singleflight usage refresh for at most one affected account per request. The normal staggered scheduler SHALL remain responsible for considering the other accounts. An incomplete usage-share snapshot MUST NOT enter the ordinary 60-second API-key authentication cache, so the next request can observe newly written evidence and rebuild the estimate immediately. Failure to create or schedule that best-effort refresh MUST NOT convert the fail-open admission into a request failure.

#### Scenario: Noncanonical long-window duration fails open

- **GIVEN** a secondary or monthly usage row reports a duration that does not match that canonical quota window
- **WHEN** usage-share evidence is evaluated
- **THEN** that account's estimate is unavailable and admission fails open
- **AND** the malformed duration cannot widen the demand scan or pair one window's percentage with another window's capacity

#### Scenario: Future-dated evidence does not create a false cap

- **GIVEN** a persisted long-window sample is timestamped after the policy snapshot's evidence-read time
- **WHEN** usage-share evidence is evaluated
- **THEN** that account's estimate is unavailable and admission fails open
- **AND** a sample committed during policy loading remains valid because evaluation time is taken after the usage reads

#### Scenario: Post-sample demand does not inflate attribution

- **GIVEN** a request-log row is timestamped after the contributing usage sample was recorded
- **WHEN** the key's proportional demand is calculated for that sample
- **THEN** that later row contributes to neither the numerator nor the denominator

#### Scenario: A sample cannot predate its reported quota window

- **GIVEN** a fresh long-window sample whose derived window start is later than its own recording time
- **WHEN** usage-share evidence is evaluated
- **THEN** that account's estimate is unavailable and admission fails open

#### Scenario: Missing evidence does not falsely block or burst refreshes

- **GIVEN** a configured key's pool contains many accounts without complete fresh long-window usage evidence
- **WHEN** quota-consuming subscription work reaches usage-share admission
- **THEN** the usage-share policy allows it
- **AND** schedules at most one immediate per-account usage refresh
- **AND** leaves the other accounts to the normal staggered scheduler

#### Scenario: Fresh evidence is observed without waiting for the auth-cache TTL

- **GIVEN** an HTTP request built an incomplete usage-share snapshot and was admitted fail-open
- **AND** the requested usage refresh subsequently writes the missing long-window evidence
- **WHEN** the key authenticates on its next request
- **THEN** the policy snapshot is rebuilt from the fresh rows
- **AND** the prior incomplete snapshot does not remain cached for the ordinary 60-second TTL

#### Scenario: Reauth token expiry invalidates cached capacity

- **GIVEN** a `reauth_required` account contributes capacity because its stored access token is still routable
- **WHEN** that token reaches its known expiry before the ordinary cache TTL, usage reset, or evidence-freshness boundary
- **THEN** the cached API-key policy snapshot is rebuilt
- **AND** the expired account no longer contributes allocation capacity

#### Scenario: Request refresh can read usage from a reauthentication-required account

- **GIVEN** a `reauth_required` account remains request-routable with an unexpired stored access token
- **AND** usage-share admission finds its long-window evidence incomplete
- **WHEN** the existing request-triggered refresh runs for that account
- **THEN** it may query usage with the stored access token
- **AND** it MUST NOT attempt an OAuth refresh-token exchange
- **AND** an authorization or permanent client failure remains a best-effort refresh failure
- **AND** it neither fails the admitted request nor changes the account status

#### Scenario: A request storm coalesces refreshes

- **GIVEN** many share checks observe the same stale account inside the request-refresh debounce window
- **WHEN** they request a refresh
- **THEN** the existing refresh path performs at most one immediate upstream fetch for that account

#### Scenario: Refresh scheduling failure preserves fail-open admission

- **GIVEN** usage-share admission has incomplete evidence and permits the request
- **WHEN** creating or scheduling the best-effort immediate refresh fails
- **THEN** the original request remains admitted
- **AND** the refresh failure is reported without leaking an unscheduled coroutine

#### Scenario: Monthly-only evidence does not use stale weekly data

- **GIVEN** an account's current plan has monthly capacity
- **AND** no fresh monthly row exists
- **WHEN** a lingering secondary row from another plan remains stored
- **THEN** the usage-share estimate is unavailable for that account
- **AND** the secondary row is not substituted for the required monthly evidence

#### Scenario: Weekly quota explicitly reported in primary remains valid

- **GIVEN** upstream explicitly reports a weekly-duration quota in the primary slot
- **WHEN** the canonical weekly-primary normalization selects that row
- **THEN** usage-share estimation treats it as the account's current long window

#### Scenario: Later weekly-primary evidence supersedes monthly residue

- **GIVEN** an account with monthly capacity has a previously recorded monthly row
- **AND** a later fetch explicitly reports a weekly-duration quota in the primary slot
- **WHEN** the canonical weekly-primary normalization selects that row
- **THEN** usage-share estimation uses the later weekly-primary window
- **AND** the older monthly row does not control the estimate
