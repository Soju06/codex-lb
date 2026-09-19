## ADDED Requirements

### Requirement: Probe prefix is single-use within a run and unique across runs

The system SHALL build the probe's filler prefix as a pure function of a run
nonce and a target size. Every call of one run MUST send byte-identical
prefix content, because a prefix that differs between calls can never hit and
would report isolation that does not exist. Two runs MUST NOT share prefix
content, and the nonce MUST appear in the first line, because upstream matches
on a prefix and a nonce placed later would leave the preceding content
identical to the previous run. The generated corpus MUST declare itself inert
and instruct the model to ignore it, MUST target roughly 28,000 tokens within
a bounded band, and MUST be accompanied by a token estimate that does not
understate the generated content.

#### Scenario: Two calls of one run send identical content

- **GIVEN** a probe run with a fresh nonce
- **WHEN** the prefix is built for the first call and again for a later call
- **THEN** the two prefixes are byte-identical

#### Scenario: A later run cannot match an earlier run's cached prefix

- **GIVEN** two probe runs with different nonces
- **WHEN** their prefixes are built
- **THEN** the first line already differs
- **AND** no generated filler line appears in both

#### Scenario: The corpus tells the model to ignore it

- **WHEN** a probe prefix is built
- **THEN** it opens and closes with an explicit reference-corpus marker carrying the run nonce
- **AND** it states that the block carries no instruction and must not be acted on

### Requirement: Probe calls reuse the routed upstream path

The system SHALL issue every probe call through the same credential refresh,
upstream proxy route resolution and streaming upstream client that ordinary
routed traffic uses, and MUST NOT open its own transport. A call whose account
is not active, whose credentials cannot be refreshed, or whose upstream proxy
route is unavailable MUST be reported as a failed call with a sanitized error
code rather than aborting the run. Calls MUST be issued sequentially with the
seed account's repetitions first, so the seed has warmed the prefix before any
other account requests it. The probe MUST request a minimal output budget,
because it measures input caching.

#### Scenario: Seed calls precede sibling calls

- **GIVEN** a run configured with two seed repetitions and three other accounts
- **WHEN** the run executes
- **THEN** the upstream receives five calls in order: the seed twice, then each other account once

#### Scenario: A failing sibling does not abort the run

- **GIVEN** a run in which one other account's call fails upstream
- **WHEN** the run completes
- **THEN** that call is reported with status `error` and its error code
- **AND** the remaining calls still ran

#### Scenario: An unclassified transport failure is reported, not raised

- **GIVEN** a run in which one account's call raises an unclassified transport error
- **WHEN** the run completes
- **THEN** that call is reported with status `error` and a generic failure code
- **AND** the reported detail is the failure's type rather than its message
- **AND** the rows collected before it are still returned

#### Scenario: The upstream stream is closed before the next call starts

- **GIVEN** a probe call that returns as soon as the completion event arrives
- **WHEN** the call returns
- **THEN** the upstream stream has already been closed

### Requirement: Running the probe requires explicit confirmation and stays capped

The system SHALL expose the planned accounts, the planned call count and the
estimated input-token cost before any call is issued. A run request that does
not carry an explicit affirmative confirmation MUST be rejected without
issuing any upstream call and without consuming the run budget. Seed
repetitions and other-account count MUST each be capped, and the other-account
count MUST be treated as an upper bound so a pool with fewer accounts still
runs rather than being refused. The run endpoint MUST be rate-limited on a
budget shared across operators and across replicas, because the protected
resource is the pool's quota rather than any one caller, and MUST be gated by
the same authorization as other operational write actions. At most one run
MUST be in flight per replica. Cross-replica serialization is deliberately not
required: the shared budget already bounds total spend, and two runs cannot
contaminate each other's measurement because each generates its own nonce and
therefore its own prefix.

#### Scenario: An unconfirmed run spends nothing

- **WHEN** an operator posts a run request whose confirmation flag is absent or false
- **THEN** the request is rejected as requiring confirmation
- **AND** no upstream call is issued
- **AND** the shared run budget is not consumed

#### Scenario: A request beyond the caps is rejected before any call

- **WHEN** an operator requests more seed repetitions or more other accounts than the caps allow
- **THEN** the request is rejected
- **AND** no upstream call is issued

#### Scenario: A smaller pool runs with the accounts it has

- **GIVEN** a pool with one seed and two other active accounts
- **WHEN** an operator requests the maximum number of other accounts
- **THEN** the run issues its seed calls plus one call per available other account

#### Scenario: The shared budget throttles repeated runs

- **GIVEN** the shared hourly run budget is exhausted
- **WHEN** another confirmed run is requested
- **THEN** the request is rejected as rate-limited
- **AND** no upstream call is issued

### Requirement: The probe refuses to spend quota while the pool is under pressure

The system SHALL refuse a run, before issuing any upstream call, when the
proxy is in degraded mode, when every account circuit breaker is open, when
fewer than two accounts are active, or when at least a quarter of the routable
accounts are rate-limited or quota-exceeded. Paused, deactivated and
reauthentication-required accounts are operator or credential state rather
than pressure and MUST be excluded from that denominator. The refusal MUST
carry a machine-readable reason, and the same assessment MUST be reported by
the pre-run plan so the operator sees the refusal before confirming.

#### Scenario: A throttled pool refuses the run

- **GIVEN** two of eight routable accounts are rate-limited or quota-exceeded
- **WHEN** a confirmed run is requested
- **THEN** the run is refused with a pool-under-pressure reason
- **AND** no upstream call is issued

#### Scenario: A pool too small to answer the question refuses the run

- **GIVEN** fewer than two accounts are active
- **WHEN** a confirmed run is requested
- **THEN** the run is refused with an insufficient-accounts reason

#### Scenario: Paused accounts are not read as pressure

- **GIVEN** four active accounts alongside paused, deactivated and reauthentication-required accounts
- **WHEN** the pool is assessed
- **THEN** the pool is not under pressure

### Requirement: The probe reports numeric results and an honest verdict

The system SHALL report one row per call carrying the sequence number, the
account identifier and label, whether the account was the seed or another
account, the call status, the reported `input_tokens` and
`input_tokens_details.cached_tokens`, the latency and any error code. A call
MUST count as a cache hit only when its cached tokens are at least half of its
input tokens, so incidental caching of the instruction preamble is not read as
a hit.

The verdict MUST be `cross_account_sharing` when any non-seed call is a hit,
because that proves the upstream prefix cache is not partitioned per account.
It MUST be `inconclusive` when no seed call hit -- there is then no evidence
the prefix was cacheable at all -- or when every non-seed call failed. It MUST
be `no_cross_account_hit` otherwise, and that verdict MUST NOT be presented as
proof of isolation, because hits are sporadic for every caller. A miss on the
seed account MUST NOT be treated as a probe failure, and the operator-facing
surface MUST say so.

#### Scenario: A sibling hit proves the cache is shared

- **GIVEN** a completed run in which one other account reported cached tokens at or above the hit threshold
- **WHEN** the result is reported
- **THEN** the cross-account-hit headline is true
- **AND** the verdict is `cross_account_sharing`

#### Scenario: A run where nothing cached is inconclusive

- **GIVEN** a completed run in which no seed call reported a cache hit
- **AND** no other account reported one either
- **WHEN** the result is reported
- **THEN** the verdict is `inconclusive`

#### Scenario: Only the seed caching reports no cross-account hit

- **GIVEN** a completed run in which a seed call hit and every other account missed
- **WHEN** the result is reported
- **THEN** the verdict is `no_cross_account_hit`

#### Scenario: Partial caching below the threshold is a miss

- **GIVEN** a call whose cached tokens are just under half its input tokens
- **WHEN** the result is reported
- **THEN** that call's status is `miss`

### Requirement: The probe persists no generated content

The system SHALL keep the run nonce, the generated prefix and every byte of
upstream response content in memory only. Nothing but numeric results,
account identifiers, statuses and the run identifier MAY be written to the
database, logs or the audit trail. When the conversation archive is enabled,
the probe's own upstream calls MUST be excluded from it for the duration of
the run, and the exclusion MUST NOT change the stored setting or affect
concurrent real traffic.

#### Scenario: The audit record carries only numbers

- **WHEN** a run completes and is audited
- **THEN** the audit details carry the run identifier, model, verdict, account identifier and token counts
- **AND** they carry neither the nonce nor any generated or returned text

#### Scenario: An enabled conversation archive does not record the probe

- **GIVEN** the conversation archive is enabled
- **WHEN** a run issues its upstream calls
- **THEN** no archive record is written for them
- **AND** the archive is active again for ordinary traffic once the run finishes
