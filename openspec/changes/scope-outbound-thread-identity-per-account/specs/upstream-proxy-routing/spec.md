## ADDED Requirements

### Requirement: Outbound thread identity must be account-scoped

The identifiers that name one logical downstream thread are account-scoped
resources upstream, so a pooled account MUST NOT present another account's
value for them. When `account_scoped_thread_identity_enabled` is on, codex-lb
MUST rewrite, at the final dispatch boundary of every Responses egress it
serves from a selected account, the outbound `prompt_cache_key` and the
session/thread header vocabulary — `session_id`, `session-id`,
`x-codex-session-id`, `x-codex-conversation-id`, `thread-id`,
`x-parent-session-id`, `x-opencode-session`, `x-session-id`,
`x-session-affinity`, and the `x-codex-parent-thread-id` /
`x-codex-window-id` pair wherever it appears as a header or as its
`client_metadata` mirror — into the selected account's namespace.

The rewrite MUST be deterministic: the same (serving account, original value)
pair MUST always produce the same output, in every worker and across restarts,
with no randomness, no clock and no process state, so a thread that stays on an
account keeps one stable upstream cache anchor. Two different accounts MUST NOT
produce the same output for the same original value. A UUID-shaped input MUST
produce a UUID-shaped output.

The salt MUST be the serving account's per-seat `codex_installation_id`. It
MUST NOT be `chatgpt_account_id`, which is the workspace identity shared by
every seat of a Team or Business organisation and is nullable; salting with it
would leave same-workspace seats presenting identical values.

The rewrite MUST be applied exactly once per outbound request. It is not
idempotent, so it MUST NOT be placed inside a helper that a dispatch path may
invoke more than once on the same headers or body; two transports serving one
thread on one account MUST arrive at the same scoped value.

`x-codex-turn-state` and `previous_response_id` MUST NOT be rewritten. They are
upstream-issued opaque values that must round-trip verbatim.

The rewrite MUST NOT be observable by account selection. The value that sticky
selection, continuity storage and the durable bridge operation fingerprint read
MUST remain the account-neutral one, so selection cannot shatter into
per-account lanes and a recovery lookup cannot miss after an account swap.

When the setting is off, every outbound request MUST be byte-for-byte identical
to what the same request produced before this capability existed.

#### Scenario: Two accounts never present the same thread identifier

- **GIVEN** `account_scoped_thread_identity_enabled` is on
- **AND** one logical thread is served first by account `A` and then by account `B`
- **WHEN** codex-lb dispatches each turn upstream
- **THEN** the `prompt_cache_key` and session/thread headers account `A` sends differ from those account `B` sends
- **AND** neither equals the value the client supplied

#### Scenario: The same account produces the same value across turns

- **GIVEN** `account_scoped_thread_identity_enabled` is on
- **AND** two turns of one thread are both served by account `A`
- **WHEN** codex-lb dispatches each turn upstream
- **THEN** both requests carry the identical scoped `prompt_cache_key` and session/thread headers

#### Scenario: A UUID-shaped identifier stays UUID-shaped

- **GIVEN** a client supplies a canonical 36-character UUID as its session identifier
- **WHEN** the value is scoped to the serving account
- **THEN** the forwarded value is also a canonical 36-character UUID
- **AND** it differs from the supplied value

#### Scenario: Continuity tokens are never rewritten

- **GIVEN** `account_scoped_thread_identity_enabled` is on
- **AND** a request carries `x-codex-turn-state` and a body `previous_response_id`
- **WHEN** codex-lb dispatches it upstream
- **THEN** both values reach upstream byte-for-byte unchanged

#### Scenario: The setting off is a no-op

- **GIVEN** `account_scoped_thread_identity_enabled` is off
- **WHEN** codex-lb builds the upstream headers and body for a request
- **THEN** every header name, header value, header order and body byte is identical to the result produced without this capability

#### Scenario: Selection reads the account-neutral value

- **GIVEN** `account_scoped_thread_identity_enabled` is on
- **WHEN** the sticky key for a Responses request is computed
- **THEN** it is identical to the key computed with the setting off
- **AND** the durable bridge operation fingerprint for the same request is also identical
