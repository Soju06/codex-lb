## MODIFIED Requirements

### Requirement: Responses balance authorized equivalent model sources

Portable HTTP Responses requests without credential-bound history MUST distribute among enabled Responses-capable sources declaring the exact public model and permitted by the presenting API key. Selection MUST prefer fewer in-flight dispatches and rotate equally loaded sources, exclude saturated or cooling sources, and apply each selected source's own alias and credential. State MUST be bounded and replica-local. Source membership MUST remain distinct from availability so unavailable pools do not fall through to subscription accounts. Single-source requests MUST retain their admission policy and accept externally created state when there is no conflicting ownership evidence.

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

Before returning a Responses stream or non-stream response, eligible portable requests MAY retry explicit upstream 401/403, 429 or 5xx rejections and proven connection failures on another eligible source. Retry MUST visit at most five distinct sources, recheck candidate availability, finalize the prior reservation and release its admission before updating cooldown or creating another reservation, and log each dispatched attempt. Exhaustion MUST preserve the final upstream error envelope and Retry-After. Credential, rate and transient failures SHALL temporarily cool the failed source with a bounded Retry-After policy. Replacing a source's key or endpoint MUST clear stale cooldown. Single-source source-error passthrough MUST remain unchanged except for the explicit redirect and ownership persistence requirements.

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

#### Scenario: Credential-bound history cannot rotate

- **WHEN** a request includes conversation state, encrypted reasoning, item references or other non-portable state
- **THEN** a multi-source pool MUST resolve unambiguous ownership or decline without dispatch
- **AND** the request MUST NOT fail over to another credential
- **AND** conflicting references and credential replacement MUST fail closed

## ADDED Requirements

### Requirement: Direct source ownership is durable before delivery

Before exposing a source response identifier, output item reference, encrypted output or successful completion, the proxy MUST durably record its owning source within the presenting API-key and public-model scope. It MUST store fingerprints rather than raw encrypted content. Persistence failure MUST withhold the corresponding successful output and finalize the dispatch without replay. Ownership MUST be available across replicas before accounting logs finish. Records SHALL expire after 30 days without refresh and SHALL be purged by retention cleanup. Existing response logs SHALL remain usable for historical response-ID ownership. Single-source requests SHALL retain legacy acceptance of externally created state and record successful state for later pool use.

#### Scenario: Immediate continuation on another replica

- **WHEN** a client continues a delivered response before the preceding request-log write finishes
- **THEN** the new request resolves the same source and does not fail merely because that log is pending

#### Scenario: Encrypted output remains on its credential

- **WHEN** a response generated by one source is replayed with encrypted reasoning on another replica
- **THEN** its recorded source receives the request even when another source has less load
- **AND** an unavailable or changed owner does not cause cross-credential replay

#### Scenario: Ownership storage fails

- **WHEN** durable ownership cannot be recorded
- **THEN** the affected successful output is withheld and a source ownership error is returned
- **AND** quota/admission cleanup completes without trying another source

#### Scenario: Output references appear before completion

- **WHEN** a stream exposes an item ID in a delta or content frame before completion
- **THEN** ownership MUST be durable before that frame is delivered, including frames synthesized by the public normalizer
- **AND** replayed output IDs on messages and tool calls MUST resolve ownership and detect conflicting references

#### Scenario: Cancellation after completed JSON generation

- **WHEN** a JSON request is cancelled while publishing ownership after upstream usage has been captured
- **THEN** cleanup MUST settle that usage under the existing cancellation policy, release admission and await the ownership write without replay

#### Scenario: Retention races with another backend

- **WHEN** one backend renews an expired ownership record while another backend is pruning it
- **THEN** a renewal committed before deletion MUST preserve the now-live record

### Requirement: Responses redirects do not authorize replay

Responses forwarding MUST NOT automatically follow upstream redirects. A redirect SHALL produce a non-retryable 502 source redirect error without forwarding Location to the client, regardless of streaming mode. Other source protocol routes SHALL retain their existing redirect behavior.

#### Scenario: Accepted POST redirects to an unreachable result

- **WHEN** a source receives a Responses POST and redirects to an unreachable result URL
- **THEN** no redirected request or second generation POST is sent
- **AND** the original attempt releases its quota reservation and admission
