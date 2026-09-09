# outbound-http-clients Delta

## MODIFIED Requirements

### Requirement: Non-native upstream requests use the Codex CLI client fingerprint

The service MUST normalize the outbound client fingerprint to the first-party
Codex CLI (`codex_cli_rs`) persona when forwarding a proxied request to the
upstream Codex backend that did not originate from a native Codex client. This
normalization MUST apply on every upstream egress path: the http builder, the
internal auto-transport websocket builder, and the client-facing
`/v1/responses` websocket egress builder
(`app/core/clients/proxy_websocket.py`). With `upstream_stream_transport="auto"`
a non-native client carrying a `x-codex-turn-state` continuity header is routed
onto the internal websocket path, and a direct websocket SDK caller reaches
upstream through the `/v1/responses` egress builder; normalizing only a subset
of these paths would let the un-normalized path reach upstream with its
downgraded fingerprint intact. The service MUST NOT modify the fingerprint of
native Codex client requests on any transport.

A request is considered **native** when its inbound `User-Agent` begins with a
known Codex client token (`codex_cli_rs`, `codex-tui`, `codex_exec`,
`codex_sdk_ts`, `codex_vscode`, `Codex Desktop`, or a value starting with
`Codex `) OR it carries an `originator` header whose value is in the native
Codex originator set (which MUST include every first-party originator the
backend whitelists, e.g. `codex_cli_rs`, `codex_vscode`, `codex_sdk_ts`).
Transport/continuity headers (`x-codex-turn-state` and other `x-codex-*`
stream headers) MUST NOT be treated as a native signal, because a non-native
client replays the upstream-issued `x-codex-turn-state` token for continuity;
treating it as native would let that follow-up reach upstream with its
downgraded fingerprint intact.

For a non-native request, the service MUST:

- Set the outbound `User-Agent` to
  `codex_cli_rs/<version> (<os>; <arch>) <terminal>`, where `<version>` is the
  cached Codex client version (falling back to the configured client-version
  default when no cached version is available) and `<os>`, `<arch>`,
  `<terminal>` are operator-configurable with defaults `Mac OS 26.5.0`,
  `arm64`, and `iTerm.app/3.6.10`.
- Remove SDK-only fingerprint headers `x-openai-client-version`,
  `x-openai-client-os`, `x-openai-client-arch`, `x-openai-client-id`, and
  `x-openai-client-user-agent`, as well as every `x-stainless-*` header (the
  OpenAI SDK fingerprint family the API layer uses to detect SDK callers).
- Remove inbound `originator` and `version` headers case-insensitively, then
  set `originator: codex_cli_rs` and `version: <version>`, where `<version>` is
  the same cached Codex client version used in the outbound `User-Agent`.
- Emit the upstream account header as PascalCase `ChatGPT-Account-Id`.
- Preserve continuity headers (`x-codex-turn-state` and other `x-codex-*`
  stream headers) on the outbound request so sticky routing is unaffected.

Resolving the fingerprint version for an outbound request MUST NOT perform a
blocking network call on the request path; the version is read from an
in-process cache. Every replica MUST warm that cache when its model refresh
loop starts and MUST refresh it on every loop tick regardless of scheduler
leadership (the lookup is a public release lookup that carries no account
credential), so a non-leader replica never presents the configured
client-version fallback beyond its first tick. A warm-up failure MUST NOT stop
the model refresh tick. The configured fallback (`model_registry_client_version`)
SHALL track the current public Codex CLI release.

#### Scenario: non-native SDK http request is rewritten to the Codex CLI fingerprint

- **WHEN** an http upstream request arrives with `User-Agent: OpenAI/Python 2.24.0`,
  untrusted `originator` / mixed-case `Version` values, and
  `x-openai-client-version` / `x-openai-client-os` / `x-stainless-os` headers
- **THEN** the outbound `User-Agent` is `codex_cli_rs/<version> (Mac OS 26.5.0; arm64) iTerm.app/3.6.10`
- **AND** the `x-openai-client-version`, `x-openai-client-os`,
  `x-openai-client-arch`, `x-openai-client-id`, and `x-openai-client-user-agent`
  headers are absent from the outbound request
- **AND** every `x-stainless-*` header is absent from the outbound request
- **AND** the only outbound identity values are `originator: codex_cli_rs` and
  `version: <version>` matching the version embedded in `User-Agent`

#### Scenario: native Codex http request is left unchanged

- **WHEN** an http upstream request arrives with `User-Agent: codex_exec/0.142.1 (Mac OS 27.0.0; arm64) unknown (codex_exec; 0.142.1)`
- **THEN** the outbound `User-Agent` equals the inbound `User-Agent`
- **AND** the request fingerprint is not normalized

#### Scenario: first-party Codex SDK request is left unchanged

- **WHEN** an http upstream request carries an `originator: codex_sdk_ts` header
  (a first-party originator the backend whitelists)
- **THEN** the outbound request is treated as native
- **AND** its `User-Agent` and `originator` header are not rewritten or stripped

#### Scenario: non-native request replaying a continuity token is still normalized

- **WHEN** an http upstream request arrives with `User-Agent: OpenAI/Python 2.24.0`
  and an `x-codex-turn-state` continuity header
- **THEN** the request is treated as non-native and its fingerprint is normalized
- **AND** the `x-codex-turn-state` header is preserved on the outbound request

#### Scenario: non-native websocket request carrying a continuity token is normalized

- **WHEN** the upstream stream transport resolves to websocket for a non-native
  request with `User-Agent: OpenAI/Python 2.24.0`, an `x-openai-client-version`
  header, and an `x-codex-turn-state` continuity header
- **THEN** the outbound websocket `User-Agent` is
  `codex_cli_rs/<version> (Mac OS 26.5.0; arm64) iTerm.app/3.6.10`
- **AND** SDK identity headers are absent and the outbound request carries
  `originator: codex_cli_rs` and `version: <version>`
- **AND** the upstream account id is carried under the PascalCase header name
  `ChatGPT-Account-Id`
- **AND** the `x-codex-turn-state` header is preserved on the outbound request

#### Scenario: native Codex websocket request is left unchanged

- **WHEN** the upstream stream transport resolves to websocket for a request
  with `User-Agent: codex_cli_rs/0.142.0 (Mac OS 27.0.0; arm64) iTerm.app/3.6.10`
- **THEN** the outbound websocket `User-Agent` equals the inbound `User-Agent`
- **AND** the account id is carried under the lowercase header `chatgpt-account-id`

#### Scenario: non-native client-facing responses websocket request is normalized

- **WHEN** a non-native SDK connects directly to the `/v1/responses` websocket
  endpoint with `User-Agent: OpenAI/Python 2.24.0`, `x-openai-client-version`,
  and `x-stainless-*` headers
- **THEN** the upstream responses websocket `User-Agent` is
  `codex_cli_rs/<version> (Mac OS 26.5.0; arm64) iTerm.app/3.6.10`
- **AND** the `x-openai-client-*` and `x-stainless-*` headers are absent while
  `originator: codex_cli_rs` and `version: <version>` are present
- **AND** the upstream account id is carried under the PascalCase header name
  `ChatGPT-Account-Id`
- **AND** the required responses websocket beta header is still present

#### Scenario: account header uses Codex CLI casing on a normalized request

- **WHEN** a non-native http request is normalized and an upstream account id is present
- **THEN** the outbound request carries the account id under the PascalCase
  header name `ChatGPT-Account-Id`

#### Scenario: per-account upstream diagnostics survive normalization

- **WHEN** upstream request logging is enabled and a normalized non-native
  request carries its account id under the PascalCase `ChatGPT-Account-Id` header
- **THEN** the upstream request start/complete log entries record the account id
  rather than `None`, so per-account diagnostics are preserved regardless of the
  header casing produced by normalization

#### Scenario: fingerprint version falls back to the configured default

- **WHEN** the Codex version cache has no cached version
- **AND** a non-native http request is normalized
- **THEN** the outbound `User-Agent` uses the configured client-version default
  for `<version>`
- **AND** the outbound `version` header uses that same configured default
- **AND** resolving the version does not perform a network call on the request path

#### Scenario: Non-leader replica warms the client version itself

- **GIVEN** a replica that does not hold the scheduler leader lease (for example the live color of a blue/green pair whose standby still holds the lease)
- **WHEN** its model refresh loop ticks
- **THEN** it fetches and caches the current Codex client version before the leader-gated model refresh
- **AND** non-native requests it forwards carry that version in `User-Agent` and `version` instead of the configured fallback

#### Scenario: Version warm-up failure does not stop the refresh tick

- **GIVEN** the public release lookup fails on a replica
- **WHEN** its model refresh loop ticks
- **THEN** the failure is logged, the cached or fallback version is kept, and the leader-gated refresh (or non-leader reconcile) still runs
