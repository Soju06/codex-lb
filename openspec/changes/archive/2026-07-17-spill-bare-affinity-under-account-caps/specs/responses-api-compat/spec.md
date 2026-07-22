## MODIFIED Requirements

### Requirement: Responses requests with input_file.file_id route to the upload's account

A `/v1/responses`, `/backend-api/codex/responses`, or `/responses/compact` request that references an `{type: "input_file", file_id}` content item SHALL be routed to the upstream account that registered the file via `POST /backend-api/files` when an in-memory pin for that `file_id` is still live. A live file pin is hard ownership evidence: it MUST override prompt-cache or bare process-session locality and MUST agree with independently resolved turn-state, previous-response, bridge, or other hard ownership.

When multiple `file_id`s are referenced, all live pins MUST resolve to the same account. If at least one ID has a live pin and another ID has no live pin, the request MUST fail with `file_owner_unavailable`; if live pins resolve to different accounts, it MUST fail with `continuity_owner_conflict`. If none of the referenced IDs has a live pin, the proxy MUST preserve compatibility with files registered directly upstream or before the current process observed the upload by forwarding the opaque IDs verbatim under ordinary unpinned routing.

#### Scenario: file_id pin drives routing for an input_file response

- **GIVEN** a `POST /backend-api/files` registered `file_xyz` through `account_a`
- **WHEN** a `/v1/responses` request references `{"type": "input_file", "file_id": "file_xyz"}`
- **THEN** the proxy MUST route the request to `account_a`

#### Scenario: file_id pin overrides prompt-cache locality

- **GIVEN** a pinned `file_xyz -> account_a`
- **WHEN** a `/v1/responses` request references `file_xyz` AND sets an explicit `prompt_cache_key`
- **THEN** the proxy MUST route to `account_a` and MUST NOT send the account-scoped file to the prompt-cache account

#### Scenario: opaque file_id without a live pin remains compatible

- **GIVEN** a request references a `file_id` registered directly upstream or before the current process observed its upload
- **AND** no referenced file has a live in-memory pin
- **WHEN** the request is routed
- **THEN** the proxy MUST forward the `file_id` verbatim under ordinary unpinned routing
- **AND** it MUST NOT reject the request solely because local owner metadata is absent
