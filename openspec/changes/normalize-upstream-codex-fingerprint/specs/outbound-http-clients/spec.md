# Normalize non-native upstream requests to the Codex CLI client fingerprint

## ADDED Requirements

### Requirement: Non-native upstream http requests use the Codex CLI client fingerprint

The service MUST normalize the outbound client fingerprint to the first-party
Codex CLI (`codex_cli_rs`) persona when forwarding a proxied http request to
the upstream Codex backend that did not originate from a native Codex client.
The service MUST NOT modify the fingerprint of native Codex client requests,
and MUST NOT modify websocket upstream requests.

A request is considered **native** when its inbound `User-Agent` begins with a
known Codex client token (`codex_cli_rs`, `codex-tui`, `codex_exec`,
`codex_vscode`, `Codex Desktop`, or a value starting with `Codex `) OR it
already carries native Codex transport headers (an `originator` header whose
value is in the native Codex originator set, or any `x-codex-*` stream header).

For a non-native http request, the service MUST:

- Set the outbound `User-Agent` to
  `codex_cli_rs/<version> (<os>; <arch>) <terminal>`, where `<version>` is the
  cached Codex client version (falling back to the configured client-version
  default when no cached version is available) and `<os>`, `<arch>`,
  `<terminal>` are operator-configurable with defaults `Mac OS 26.5.0`,
  `arm64`, and `iTerm.app/3.6.10`.
- Remove SDK-only fingerprint headers `x-openai-client-version`,
  `x-openai-client-os`, `x-openai-client-arch`, `x-openai-client-id`, and
  `x-openai-client-user-agent`.
- Remove any inbound `originator` header and MUST NOT add an `originator`
  header, matching the Codex CLI behavior of omitting the header when the
  originator equals the default `codex_cli_rs`.
- Emit the upstream account header as PascalCase `ChatGPT-Account-Id`.

Resolving the fingerprint version for an outbound request MUST NOT perform a
blocking network call on the request path; the version is read from an
in-process cache that is refreshed by existing background refresh paths.

#### Scenario: non-native SDK http request is rewritten to the Codex CLI fingerprint

- **WHEN** an http upstream request arrives with `User-Agent: OpenAI/Python 2.24.0`
  and `x-openai-client-version` / `x-openai-client-os` headers
- **THEN** the outbound `User-Agent` is `codex_cli_rs/<version> (Mac OS 26.5.0; arm64) iTerm.app/3.6.10`
- **AND** the `x-openai-client-version`, `x-openai-client-os`,
  `x-openai-client-arch`, `x-openai-client-id`, and `x-openai-client-user-agent`
  headers are absent from the outbound request
- **AND** no `originator` header is present on the outbound request

#### Scenario: native Codex http request is left unchanged

- **WHEN** an http upstream request arrives with `User-Agent: codex_exec/0.142.1 (Mac OS 27.0.0; arm64) unknown (codex_exec; 0.142.1)`
- **THEN** the outbound `User-Agent` equals the inbound `User-Agent`
- **AND** the request fingerprint is not normalized

#### Scenario: request carrying native Codex transport headers is treated as native

- **WHEN** an http upstream request arrives with a non-Codex `User-Agent`
  but includes an `x-codex-turn-state` header
- **THEN** the request is treated as native and its fingerprint is not normalized

#### Scenario: account header uses Codex CLI casing on a normalized request

- **WHEN** a non-native http request is normalized and an upstream account id is present
- **THEN** the outbound request carries the account id under the PascalCase
  header name `ChatGPT-Account-Id`

#### Scenario: fingerprint version falls back to the configured default

- **WHEN** the Codex version cache has no cached version
- **AND** a non-native http request is normalized
- **THEN** the outbound `User-Agent` uses the configured client-version default
  for `<version>`
- **AND** resolving the version does not perform a network call on the request path
