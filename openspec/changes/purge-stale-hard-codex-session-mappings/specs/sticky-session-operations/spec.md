# sticky-session-operations Delta Specification

## MODIFIED Requirements

### Requirement: Sticky sessions are explicitly typed

The system SHALL persist each sticky-session mapping with an explicit kind so durable Codex backend affinity, durable dashboard sticky-thread routing, and bounded prompt-cache affinity can be managed independently. Budget-pressure reallocation MUST apply only to mappings whose kind/source is soft. A raw or legacy `codex_session` mapping MUST remain owner-bound because it may represent explicit turn-state continuity; budget pressure MUST NOT delete or rebind it. Request-time selection MUST NOT reallocate a hard `codex_session` mapping to a different account under any circumstance, including when its owner is unavailable. Independently of request-time selection, a periodic background job MAY retire (never rebind) a hard `codex_session` mapping once its owner has been durably unavailable — not merely transiently rate-limited or paused — for well past its own recovery point, so a future request against that session simply re-resolves fresh instead of failing closed forever. Retiring MUST happen in two phases, never a single delete: the mapping is first tombstoned (marked deliberately abandoned, not deleted), and only dropped outright after it has sat unclaimed for a further grace window. A tombstoned mapping MUST be exempt from the ambiguous-conversation-owner check, so a `conversation`-continuity request against it can select a fresh owner instead of failing closed forever with no way to recover. When the owner's own `reset_at` is known and still in the future, that recovery point MUST take priority over any flat cutoff. An account that is already unavailable when the process starts MUST still receive the full grace window from that point forward, so a merely-transient outage that predates a deploy is never purged on the first cleanup cycle after it; that seeding MUST happen at most once per database, not once per process start, so a fast-redeploying multi-replica deployment cannot perpetually reset the grace clock for a durably-dead mapping.

#### Scenario: Soft sticky reallocation uses split primary and secondary pressure thresholds
- **WHEN** a request resolves an existing prompt-cache, sticky-thread, or other explicitly soft mapping
- **AND** the pinned account is otherwise eligible to serve traffic
- **AND** the pinned account is strictly above either the configured primary sticky reallocation threshold or the configured secondary sticky reallocation threshold
- **AND** another eligible account remains at or below both configured sticky reallocation thresholds
- **THEN** selection rebinds the sticky-session mapping to the healthier account before sending the request upstream

#### Scenario: Sticky reallocation preserves a pinned account when every candidate is split-threshold pressured
- **WHEN** a request resolves an existing soft sticky-session mapping
- **AND** the pinned account is otherwise eligible to serve traffic
- **AND** the pinned account is strictly above either configured sticky reallocation threshold
- **AND** every other eligible account is also strictly above at least one configured sticky reallocation threshold
- **THEN** selection retains the existing pinned account to avoid sticky-pin thrashing

#### Scenario: Fresh selection does not apply sticky secondary pressure threshold
- **WHEN** a request has no sticky-session mapping
- **AND** one eligible account is above the configured secondary sticky reallocation threshold but below the normal primary budget threshold
- **THEN** the account remains eligible for ordinary non-sticky routing according to the selected routing strategy

#### Scenario: Hard Codex mapping ignores budget-pressure reallocation

- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A is above a sticky budget-pressure threshold
- **AND** account B has more remaining budget
- **WHEN** the request is selected
- **THEN** selection remains constrained to account A
- **AND** the raw mapping is neither deleted nor rebound to account B

#### Scenario: Unavailable hard Codex owner does not lose its mapping at request time

- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A is temporarily quota-exceeded or otherwise unusable
- **AND** account B is healthy
- **WHEN** hard-owner selection fails
- **THEN** the request fails closed instead of selecting account B
- **AND** the raw mapping is neither deleted nor rebound by that request

#### Scenario: A durably unavailable hard Codex owner's mapping is eventually tombstoned

- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A is still `PAUSED`, `RATE_LIMITED`, or `QUOTA_EXCEEDED`
- **AND** the later of the mapping's last use and account A's transition into
  an unavailable status is before a conservative cutoff
- **AND** the mapping has not already been tombstoned
- **WHEN** the periodic sticky-session cleanup job runs
- **THEN** the mapping is tombstoned (marked deliberately abandoned) rather than deleted
- **AND** it is not rebound to any other account
- **AND** the next request against that session resolves a fresh mapping instead of failing closed

#### Scenario: A tombstoned hard Codex mapping unblocks a conversation-continuity request

- **GIVEN** a hard `codex_session` mapping for a turn state has been tombstoned by the periodic cleanup job
- **AND** the request's `conversation` field is non-empty, requiring an unambiguous owner
- **AND** the account pool has more than one eligible account
- **WHEN** the request is selected
- **THEN** selection does not fail closed with the ambiguous-conversation-owner error solely because of the tombstoned mapping
- **AND** selection proceeds to choose a fresh eligible account
- **AND** the resulting mapping write clears the tombstone, restoring a normal hard mapping for that turn state

#### Scenario: An unclaimed tombstone is eventually deleted outright

- **GIVEN** a hard `codex_session` mapping was tombstoned by the periodic cleanup job
- **AND** no request has re-established an owner for it since
- **AND** the tombstone's own abandonment timestamp is before a further conservative cutoff
- **WHEN** the periodic sticky-session cleanup job runs
- **THEN** the mapping is deleted outright
- **AND** a subsequent request against that session falls back to the same fail-closed default as a session that was never seen

#### Scenario: A merely transient hard Codex owner outage is never purged

- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A became rate-limited or paused more recently than the
  conservative cutoff, even if the mapping itself is older
- **WHEN** the periodic sticky-session cleanup job runs
- **THEN** the mapping is left untouched

#### Scenario: A known future reset_at overrides the flat cutoff

- **GIVEN** a raw `codex_session` mapping points to account A
- **AND** account A is `RATE_LIMITED` or `QUOTA_EXCEEDED` with a known `reset_at` still in the future
- **AND** the mapping's timestamp is already before the conservative cutoff
- **WHEN** the periodic sticky-session cleanup job runs
- **THEN** the mapping is left untouched
- **AND** it remains eligible for purging only once `reset_at` has passed and the mapping is still stale by the cutoff

#### Scenario: An outage that predates process startup still gets its grace window

- **GIVEN** account A is already `PAUSED`, `RATE_LIMITED`, or `QUOTA_EXCEEDED` when this process starts
- **AND** its hard `codex_session` mapping's timestamp already predates the conservative cutoff, unrelated to when the outage actually began
- **AND** this is the first process, ever, to boot against this database since this behavior shipped
- **WHEN** the process completes startup
- **THEN** the mapping's timestamp is refreshed to the startup time
- **AND** the first periodic cleanup cycle after startup does not purge that mapping solely because of its pre-startup timestamp

#### Scenario: Startup seeding runs at most once per database

- **GIVEN** the one-time startup-seeding backfill has already run, on this or any other replica sharing the same database
- **WHEN** a process (the same replica restarting, or a different replica) completes startup
- **THEN** no hard `codex_session` mapping's timestamp is refreshed by this seeding pass
- **AND** an account that has remained unavailable since the original backfill is not given a fresh grace window merely by this process starting
