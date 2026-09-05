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

### Requirement: Native history and notes use the body session for account affinity

For native history and notes v2 operations, the proxy MUST use nonblank
`context.session_id` as a dedicated hard history-session identity for
account selection, shared with Responses and compact requests that carry
`history_ingest_requested: true` in their native turn metadata. Ordinary
Responses process-session affinity MUST remain unchanged. It MUST preserve API-key account scope and MUST NOT alter
the forwarded request body to synthesize a header.

The proxy MUST NOT fail over or retry a native history-or-notes operation on a
different account after selection. It MUST preserve the selected account's
upstream error instead.

#### Scenario: Notes write does not cross account boundaries after failure

- **GIVEN** an API-key-scoped native `write_file` call resolves to account A
- **AND** account A returns an upstream error
- **AND** account B is otherwise eligible
- **WHEN** the proxy handles the error
- **THEN** the proxy returns account A's error
- **AND** it does not send the write to account B

#### Scenario: History owner is unavailable

- **GIVEN** notes and marked Responses requests share account A for a process session
- **AND** account A becomes unavailable while account B is eligible
- **WHEN** another marked Responses request arrives for that session
- **THEN** it fails without selecting account B or replacing the stored owner

#### Scenario: Child threads retain process history ownership

- **GIVEN** root and child threads have distinct thread and turn-state headers
- **AND** both carry the same process session and native history-ingest marker
- **WHEN** Responses, compact, and notes requests select accounts
- **THEN** all use the same hard history-session owner
