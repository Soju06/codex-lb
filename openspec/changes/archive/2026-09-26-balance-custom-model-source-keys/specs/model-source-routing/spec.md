## ADDED Requirements

### Requirement: Responses balance authorized equivalent model sources

Unanchored HTTP Responses requests MUST distribute among enabled Responses-capable sources declaring the exact public model and permitted by the presenting API key. Selection MUST prefer fewer in-flight dispatches and rotate equally loaded sources, exclude saturated or cooling sources, and apply each selected source's own alias and credential. State MUST be bounded and replica-local. Source membership MUST remain distinct from availability so unavailable pools do not fall through to subscription accounts. Single-source requests MUST preserve existing behavior.

#### Scenario: Five credentials share one public model

- **GIVEN** five eligible sources with separate credentials and the same public alias
- **WHEN** sequential requests arrive without a previous response anchor
- **THEN** the sources share requests instead of always selecting the first name
- **AND** the client retains one endpoint, codex-lb API key and public model

#### Scenario: Scope and capacity constrain selection

- **WHEN** some matching sources are disabled, lack streaming capability, are outside the key's assignment, saturated or cooling
- **THEN** only available authorized capable sources may be dispatched
- **AND** an unavailable pool returns an OpenAI-format 503 with Retry-After without acquiring a usage reservation

### Requirement: Source failover owns and settles each attempt

Before returning a Responses stream or non-stream response, eligible unanchored requests MAY retry explicit upstream 401/403, 429 or 5xx rejections and proven connection failures on another eligible source. Retry MUST visit at most five distinct sources, recheck candidate availability, finalize the prior reservation and release its admission before updating cooldown or creating another reservation, and log each dispatched attempt. Exhaustion MUST preserve the final upstream error envelope and Retry-After. Credential, rate and transient failures SHALL temporarily cool the failed source with a bounded Retry-After policy. Replacing a source's key or endpoint MUST clear stale cooldown. Single-source behavior remains unchanged.

#### Scenario: Rejected credential advances after cleanup

- **WHEN** a source rejects credentials and another authorized source succeeds
- **THEN** the first attempt's reservation and slot are released before the second is reserved
- **AND** both source attempts are recorded and subsequent requests skip the cooling source

#### Scenario: Unsafe replay is declined

- **WHEN** a request is anchored, a stream has been returned, the client disconnects, a header/body timeout occurs, a successful upstream body is malformed or reservation finalization fails
- **THEN** the request is not replayed on another credential
- **AND** client cancellation and idle disconnects do not penalize otherwise healthy sources

### Requirement: Source response continuity preserves credential ownership

Recorded source response anchors MUST resolve within the presenting API key and public model scope and remain on the recorded source. A known owner that is disabled, removed, disallowed, saturated or cooling MUST NOT cause a switch to another source. Conflicting ownership or missing ownership in a multi-source pool MUST fail closed. Existing subscription-owned and file-pinned routing MUST remain authoritative. Ownership lookup failures MUST fail closed without dispatch.

#### Scenario: Resume on a different replica

- **GIVEN** a completed source response is recorded in shared request logs
- **WHEN** the client continues with its response ID on another replica
- **THEN** the same eligible source handles the request regardless of rotation state

#### Scenario: Unknown or inaccessible owner

- **WHEN** an anchor has no unambiguous permitted owner in a pool or its recorded owner is no longer eligible
- **THEN** the response reports unavailable continuity without contacting a different source

#### Scenario: File and subscription ownership wins

- **WHEN** the payload references an uploaded file or a subscription-owned response
- **THEN** the existing account ownership path handles the request and source pooling does not bypass its restrictions
