## MODIFIED Requirements

### Requirement: Responses requests with input_file.file_id route to the upload's account

A `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request that references an `{type: "input_file", file_id}` content item SHALL be routed to the upstream account that registered the file via `POST /backend-api/files` when a durable, unexpired pin for that `file_id` exists. The pin MUST be visible to every replica that shares the application database. A live file pin is hard ownership evidence: it MUST override prompt-cache or bare process-session locality and MUST agree with independently resolved turn-state, previous-response, bridge, or other hard ownership.

When multiple `file_id`s are referenced, all live pins MUST resolve to the same account. If at least one ID has a live pin and another ID has no live pin, the request MUST fail with `file_owner_unavailable`; if live pins resolve to different accounts, it MUST fail with `continuity_owner_conflict`. If none of the referenced IDs has a live pin, the proxy MUST preserve compatibility with files registered directly upstream or before durable ownership was observed by forwarding the opaque IDs verbatim under ordinary unpinned routing.

A live durable pin MUST NOT be reassigned to another account. Repeating the claim for the same account MUST be idempotent and MAY renew its expiry; an expired identifier MAY be claimed by a later upload.

Every hard file-owner decision MUST read the shared database and MUST NOT rely on a process-local owner cache. Authenticated inter-replica forwarding metadata MAY corroborate the freshly resolved durable owner but MUST NOT replace the receiver's database read. A missing or conflicting receiver-side durable owner MUST fail closed before account selection or upstream invocation. Pin expiry, reclaim, and cleanup MUST use database-authoritative statement time rather than a replica's application clock.

For a streaming Responses request whose durable file-owner lookup runs in the stream service, any API-key usage reservation acquired before that lookup MUST have exactly one cleanup owner if resolution fails or the request is cancelled. Within one replica, the API layer MUST own cleanup until the direct stream service enters its settlement-guarded `try/finally` or the local HTTP-bridge service successfully submits the request and installs its request-state finalizer. The service finalizer MUST own cleanup after that explicit boundary so those layers cannot both release the reservation. Merely completing the durable lookup MUST NOT transfer cleanup before a service finalizer is active, and an initial SSE heartbeat MUST NOT transfer ownership to the client.

When an authenticated HTTP-bridge origin forwards that reservation to another replica, the receiver MUST delay its successful HTTP 200 response until its service finalizer is active. That 200 response MUST be the cleanup-handoff acknowledgement that transfers ownership from the origin to the receiver. The origin MUST distinguish a request that has not been dispatched, a dispatch with no observed response status, a successful HTTP 200 acknowledgement, and a definitive non-200 rejection. Before dispatch or after a definitive non-200, receiver-side owner-revalidation failure or cancellation MUST propagate with cleanup remaining at the origin. After dispatch when no response status can be observed, the origin MUST NOT actively release or replay the reservation because the receiver may already own settlement; receiver settlement or bounded stale-reservation cleanup MUST resolve that ambiguity. After the acknowledgement, the receiver service finalizer MUST remain authoritative even if no upstream event has arrived. If a bounded startup probe hands pending preflight work to the response body and the body closes first, the active owner MUST cancel and await that work before scheduling one cancellation-safe release attempt. If that persistence write fails, the same cleanup owner MUST schedule one follow-up release attempt instead of abandoning the reservation. An SSE heartbeat or another frame MUST NOT transfer cleanup ownership. Compact service settlement MUST likewise suppress a second API-layer release after its single settlement attempt. Once a forwarded compact service has made that settlement attempt, including when both the primary finalize and the fallback release fail, a later receiver-side output validation failure or a `usage_settlement_failed` error MUST preserve HTTP 200 as the cleanup-handoff acknowledgement and surface a terminal `response.failed` event with the stable error code; it MUST NOT become a non-200 rejection that permits origin release or replay. A client disconnect after the initial SSE heartbeat MUST close the service stream even when the startup probe already completed. A cleanup-store failure MUST NOT replace a stable owner-resolution error. A cleanup-store failure MUST NOT mask the original stable owner error or cancellation. Owner-lookup failure or cancellation MUST NOT trigger account failover or another upstream attempt.

#### Scenario: file_id pin drives routing for an input_file response

- **GIVEN** a `POST /backend-api/files` registered `file_xyz` through `account_a` on one replica
- **WHEN** a `/v1/responses` request references `{"type": "input_file", "file_id": "file_xyz"}` on another replica
- **THEN** the proxy MUST route the request to `account_a`

#### Scenario: file_id pin overrides prompt-cache locality

- **GIVEN** a pinned `file_xyz -> account_a`
- **WHEN** a `/v1/responses` request references `file_xyz` AND sets an explicit `prompt_cache_key`
- **THEN** the proxy MUST route to `account_a` and MUST NOT send the account-scoped file to the prompt-cache account

#### Scenario: opaque file_id without a live pin remains compatible

- **GIVEN** a request references a `file_id` registered directly upstream or before the system durably observed its upload
- **AND** no referenced file has a live durable pin
- **WHEN** the request is routed
- **THEN** the proxy MUST forward the `file_id` verbatim under ordinary unpinned routing
- **AND** it MUST NOT reject the request solely because owner metadata is absent

#### Scenario: file finalize resolves ownership across replicas

- **GIVEN** one replica registered `file_xyz` through `account_a`
- **WHEN** another replica handles `POST /backend-api/files/file_xyz/uploaded`
- **THEN** the proxy MUST finalize the file through `account_a`
- **AND** it MUST NOT fall back to a different eligible account

#### Scenario: concurrent live ownership claims do not overwrite

- **GIVEN** `file_xyz` has a live durable pin to `account_a`
- **WHEN** another replica attempts to pin `file_xyz` to `account_b`
- **THEN** the claim MUST fail with `continuity_owner_conflict`
- **AND** subsequent routing MUST still resolve `file_xyz` to `account_a`

#### Scenario: a replica observes an expired pin reclaimed by another replica

- **GIVEN** a replica previously resolved `file_xyz` to `account_a`
- **AND** the durable pin expires and another replica claims `file_xyz` for `account_b`
- **WHEN** the first replica resolves `file_xyz` again
- **THEN** it MUST read the durable owner and return `account_b`
- **AND** it MUST NOT return `account_a` from process-local state

#### Scenario: durable owner lookup failure fails closed

- **GIVEN** a request references a file whose owner decision requires the shared database
- **WHEN** the durable owner lookup fails
- **THEN** the request MUST fail before selecting or invoking an unpinned fallback account

#### Scenario: cancellation during owner lookup releases admission state

- **GIVEN** a request has acquired an API-key usage reservation before durable file-owner resolution completes
- **WHEN** the request is cancelled while the owner lookup is pending
- **THEN** exactly one cleanup owner MUST attempt to release or settle the reservation
- **AND** no account selection, upstream invocation, retry, or failover may occur

#### Scenario: delayed owner failure after stream handoff releases admission state

- **GIVEN** the streaming startup probe expires while durable file-owner resolution is still pending
- **WHEN** the lookup later fails or the response body is closed
- **THEN** the origin API MUST cancel and await any still-pending lookup
- **AND** the origin API MUST make exactly one release attempt
- **AND** a lookup failure MUST be represented by the stable `file_owner_unavailable` error

#### Scenario: failed reservation release is retried

- **GIVEN** a startup or disconnect cleanup owns an API-key reservation
- **WHEN** the first persistence release fails
- **THEN** the cleanup owner MUST schedule one follow-up release attempt
- **AND** it MUST NOT leave the reservation reserved with no later cleanup path

#### Scenario: forwarded owner metadata is revalidated against durable ownership

- **GIVEN** a replica receives authenticated forwarding metadata that identifies `account_a` as a referenced file's owner
- **WHEN** the receiver's fresh durable lookup has no live owner or identifies a different owner
- **THEN** the receiver MUST fail closed
- **AND** it MUST NOT route using the forwarded value alone
- **AND** it MUST propagate the preflight failure to the origin without releasing the origin reservation
- **AND** the originating request path MUST remain the sole cleanup owner because no successful handoff acknowledgement was sent

#### Scenario: forwarded stream acknowledges cleanup ownership before HTTP 200

- **GIVEN** the origin forwards a file-pinned streaming request and its API-key reservation to the authenticated owner replica
- **WHEN** the receiver completes durable owner revalidation and installs its service settlement finalizer
- **THEN** the receiver MAY return HTTP 200 as the cleanup-handoff acknowledgement
- **AND** the origin MUST stop releasing the reservation after receiving that acknowledgement
- **AND** cancellation before the first upstream event MUST invoke only the receiver's service finalizer

#### Scenario: ambiguous owner dispatch defers active origin cleanup

- **GIVEN** the origin has begun dispatching a signed forwarded request carrying its reservation
- **WHEN** the transport fails before the origin can observe an HTTP status
- **THEN** the origin MUST NOT actively release or replay the reservation
- **AND** receiver settlement or stale-reservation cleanup MUST remain the only recovery paths

#### Scenario: definitive owner rejection retains origin cleanup

- **GIVEN** the origin dispatches a signed forwarded request carrying its reservation
- **WHEN** the receiver returns a non-200 response without acknowledging cleanup handoff
- **THEN** the origin MUST make exactly one cancellation-safe release attempt
- **AND** the receiver MUST NOT settle the origin reservation

#### Scenario: owner non-200 remains a rejection after body-read failure

- **GIVEN** the origin has observed a non-200 owner-forward status
- **WHEN** reading the rejection body then fails
- **THEN** the origin MUST treat the outcome as a definitive rejection
- **AND** it MUST NOT reclassify the dispatch as ambiguous

#### Scenario: compact service settlement is not released twice

- **GIVEN** terminal or direct compaction receives an API-key usage reservation
- **WHEN** the compact service makes its single settlement or release attempt
- **THEN** the API layer MUST NOT issue another release for that reservation
- **AND** a pre-service failure MUST still leave exactly one release attempt at the API layer

#### Scenario: malformed compact output after settlement preserves handoff

- **GIVEN** a forwarded terminal compact request whose receiver service has made its single settlement attempt
- **WHEN** the settled response lacks a valid compaction output item
- **THEN** the receiver MUST return HTTP 200 as the cleanup-handoff acknowledgement
- **AND** it MUST emit a terminal `response.failed` event
- **AND** the origin MUST NOT release or replay the reservation

#### Scenario: compact settlement failure after fallback preserves handoff

- **GIVEN** a forwarded terminal compact request whose receiver service has made its single settlement attempt
- **WHEN** usage settlement fails after a successful fallback release
- **THEN** the receiver MUST return HTTP 200 as the cleanup-handoff acknowledgement
- **AND** it MUST emit a terminal `response.failed` event with code `usage_settlement_failed`
- **AND** the origin MUST NOT release or replay the reservation

#### Scenario: compact settlement attempt preserves handoff when both writes fail

- **GIVEN** a forwarded terminal compact request whose receiver service attempts settlement
- **WHEN** both reservation finalization and the fallback release fail
- **THEN** the receiver MUST still return HTTP 200 as the cleanup-handoff acknowledgement
- **AND** it MUST emit a terminal `response.failed` event with code `usage_settlement_failed`
- **AND** the origin MUST NOT release or replay the reservation

#### Scenario: completed startup probe still closes the service stream

- **GIVEN** the streaming startup probe already obtained the first service event
- **WHEN** the client disconnects after the initial SSE heartbeat
- **THEN** the origin MUST close the service stream
- **AND** reservation cleanup MUST still run if ownership has not transferred
