## ADDED Requirements

### Requirement: Native history and notes v2 operations proxy through Codex control

The system MUST expose authenticated `POST` routes for
`/backend-api/codex/alpha/history/v2/list_windows`,
`/backend-api/codex/alpha/history/v2/list_items`,
`/backend-api/codex/alpha/history/v2/read_item`,
`/backend-api/codex/alpha/history/v2/search_contents`,
`/backend-api/codex/alpha/notes/v2/thread_hint`,
`/backend-api/codex/alpha/notes/v2/list_files_by_prefix`,
`/backend-api/codex/alpha/notes/v2/read_file`,
`/backend-api/codex/alpha/notes/v2/search_contents`,
`/backend-api/codex/alpha/notes/v2/append_to_file`, and
`/backend-api/codex/alpha/notes/v2/write_file`.

For every operation, the proxy MUST forward the request body and accepted
native headers unchanged to the selected upstream account. This includes
`x-openai-encrypted-tool-arguments` and
`x-openai-tool-output-truncation-policy`.

#### Scenario: Encrypted notes search reaches the selected upstream account

- **WHEN** an authenticated client posts `alpha/notes/v2/search_contents` with
  encrypted tool arguments, an output truncation policy header, and a valid
  `context.session_id`
- **THEN** the proxy forwards the same body and both headers upstream
- **AND** returns the upstream response without local notes storage or
  decryption

#### Scenario: Unsupported native operation is not relayed

- **WHEN** an authenticated client posts an `alpha/notes/v2/` or
  `alpha/history/v2/` operation name that is not one of the ten above
- **THEN** the proxy does not forward it upstream and answers as it did before
  these routes existed

### Requirement: Native history and notes use the body session for account affinity

For native history and notes v2 operations, the proxy MUST use a nonblank
body `context.session_id` as a dedicated hard `history_session` identity for
account selection. That identity MUST have no legacy raw-row interpretation
and MUST NOT spill over an account cap. It MUST preserve API-key account scope
and MUST NOT alter the forwarded request body to synthesize a header.

When no `history_session` owner exists yet, selection MUST prefer the soft
process-session owner recorded for the same `session_id` by Responses traffic,
when one exists and is available, and then persist the `history_session` row
on its own. Ordinary Responses and compact affinity MUST remain unchanged; in
particular the `history_ingest_requested` turn-metadata marker MUST NOT change
which sticky kind, source, or key a Responses or compact request resolves to.

The proxy MUST NOT fail over or retry a native history-or-notes operation on a
different account after selection. It MUST preserve the selected account's
upstream error instead. This applies even when the body carries no usable
identity.

A body that is not a JSON object MUST be rejected with `400`. A JSON object
without a nonblank `context.session_id` MUST be forwarded once with the
ordinary control-request affinity; the upstream protocol remains authoritative
for payload validation and error shape.

#### Scenario: Notes write does not cross account boundaries after failure

- **GIVEN** an API-key-scoped native `write_file` call resolves to account A
- **AND** account A returns an upstream error
- **AND** account B is otherwise eligible
- **WHEN** the proxy handles the error
- **THEN** the proxy returns account A's error
- **AND** it does not send the write to account B

#### Scenario: First native call follows the process-session owner

- **GIVEN** an ordinary Responses turn for process session `S` ran on
  account A and recorded the soft process-session owner
- **AND** no `history_session` owner exists for `S`
- **WHEN** an authenticated `thread_hint` call arrives with
  `context.session_id = S`
- **THEN** the proxy selects account A
- **AND** persists account A as the `history_session` owner of `S`

#### Scenario: History-marked Responses keep ordinary soft affinity

- **GIVEN** account A owns the `history_session` row for `S` and is paused
- **AND** account B is eligible
- **WHEN** a Responses request for `S` with
  `history_ingest_requested: true` in its turn metadata arrives
- **THEN** it is routed exactly as an unmarked request would be, so it may
  run on account B
- **AND** the `history_session` owner of `S` is not replaced

#### Scenario: History owner is unavailable

- **GIVEN** account A owns the `history_session` row for `S` and is paused
- **AND** account B is eligible
- **WHEN** a native notes or history call arrives for `S`
- **THEN** it fails without selecting account B
- **AND** the stored `history_session` owner remains account A

#### Scenario: Body without a session identity is forwarded once

- **GIVEN** an authenticated `thread_hint` body that is a JSON object without
  `context.session_id`
- **WHEN** the proxy handles it
- **THEN** it forwards the body unchanged to one selected account without
  a `history_session` pin
- **AND** it does not retry on another account
