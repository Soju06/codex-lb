# usage-refresh-policy Delta

## ADDED Requirements

### Requirement: Streaming usage-limit failures request an immediate coalesced usage refresh

When an upstream stream fails with the error code `usage_limit_reached`, the proxy
MUST request an immediate usage refresh for the failing account in addition to
marking it rate limited. The refresh MUST run as a tracked background task that
never blocks or alters the response, MUST load the account from a fresh
background-session row rather than the request's `Account` instance, and MUST
bypass the usage freshness gate. Usage refreshes run on two per-account
singleflight lanes: the background scheduler and forced refreshes use the bare
account key with caller-bound sessions, while the rate-limit payload, fleet and
request-triggered refreshes use the owned-session key. A requested refresh MUST
join a refresh already in flight on either lane rather than starting a third
concurrent upstream fetch, and concurrent requests for the same account MUST
share a single in-flight owned-session refresh without queueing a successor
fetch. Repeated requests for the same account within a fixed 15 second window
MUST be dropped. The request MUST be
skipped when usage refresh is disabled, when the account is in usage-refresh auth
cooldown, or when the fresh row is missing, `paused`, `reauth_required`, or
`deactivated`. When the requested (or joined) refresh writes usage rows it MUST
invalidate the account selection cache, so the next selection observes the new
usage evidence without waiting out the cache TTL. Plain `rate_limit_exceeded`
throttling and quota error codes MUST NOT request a refresh.

#### Scenario: A 429 storm produces a single upstream fetch

- **GIVEN** twenty concurrent streams on one account fail upstream with `usage_limit_reached`
- **WHEN** each failure requests a usage refresh
- **THEN** at most one upstream usage fetch runs for that account
- **AND** every failure's response is unaffected by the refresh

#### Scenario: A request joins the scheduler's in-flight refresh

- **GIVEN** the background scheduler is refreshing an account on the bare account singleflight key
- **WHEN** a stream on that account fails with `usage_limit_reached`
- **THEN** the requested refresh waits on the scheduler's in-flight refresh and records its outcome
- **AND** no additional upstream fetch starts

#### Scenario: A request joins an in-flight owned-session refresh

- **GIVEN** the rate-limit payload path or another request is refreshing an account on the owned-session singleflight key
- **WHEN** a stream on that account fails with `usage_limit_reached`
- **THEN** the requested refresh joins that in-flight refresh
- **AND** no additional upstream fetch starts

#### Scenario: Repeats inside the debounce window are dropped

- **GIVEN** a usage refresh was requested for an account less than 15 seconds ago
- **WHEN** another stream on that account fails with `usage_limit_reached`
- **THEN** no new refresh is requested
- **AND** a failure after the window elapses requests a refresh again

#### Scenario: The pool reports usage exhaustion on the next selection

- **GIVEN** the last selectable account's stream fails upstream with `usage_limit_reached`
- **AND** a selection between the rate-limit mark and the refresh's row write repopulated the selection cache without usage evidence
- **AND** the requested refresh writes a usage row at or above 100 % with its reset time
- **WHEN** the next request selects an account
- **THEN** the written row has invalidated the selection cache
- **AND** selection fails with the structured `usage_limit_reached` failure carrying that `resets_at`
- **AND** it does not wait for the next scheduled refresh interval or the selection cache TTL

#### Scenario: The request never mutates the streaming request's account

- **GIVEN** a stream fails with `usage_limit_reached` for an `Account` bound to the request session
- **WHEN** the requested refresh runs
- **THEN** it reads and updates only the fresh background-session row
- **AND** the request's `Account` instance is neither read nor written by the refresh

#### Scenario: Ineligible rows and disabled refresh are skipped

- **GIVEN** usage refresh is disabled, or the account is in auth cooldown, or its fresh row is missing, `paused`, `reauth_required`, or `deactivated`
- **WHEN** a stream on that account fails with `usage_limit_reached`
- **THEN** no upstream usage fetch runs
