# Change: Align native HTTP/2 wire profile with Codex

## Why

Controlled A′/A/C captures show stable direct-Codex behavior that the native
codex-lb HTTP client does not reproduce. Direct Codex advertises a 2 MiB stream
window and a 5 MiB connection receive window, while the helper advertises the
HTTP/2 defaults. codex-lb also changes the decoded order of native singleton
headers and adds a model-discovery `version` header absent from repeated direct
captures.

## What Changes

- Configure the persistent native reqwest client with the measured Codex HTTP/2
  stream window, connection window, frame-size, and header-list limits.
- Preserve the original position and spelling of native `authorization`,
  `accept`, `content-type`, and account-id headers when replacing their values.
- Emit model-discovery headers in measured Codex order and omit the redundant
  `version` header while retaining the version query parameter and User-Agent.
- Re-run the privacy-safe raw HTTP/2 A′/A/C comparison as the external
  regression proof.

## Impact

- Affected specs: `outbound-http-clients`, `compatibility-tooling`
- Affected code: native HTTP client construction, model-discovery headers,
  Responses upstream header replacement, tests and traffic parity evidence
- No new setting or operator action is introduced.
