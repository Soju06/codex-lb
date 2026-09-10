## Why

Operators configuring external providers in CLIProxyAPI must currently maintain a second model list in CodexLB. Deleting missing source models also loses ownership, allowing requests for those models to fall through to native subscription accounts.

## What Changes

- Add opt-in automatic CPA catalog acquisition through inference authentication.
- Persist validated model metadata and retain the last successful catalog during fetch failures.
- Hide successfully omitted models while retaining their unavailable source identity, and restore entries when CPA lists them again.
- Preserve native model precedence and existing Responses forwarding.

## Capabilities

### New Capabilities

- `cli-proxy-catalog`: Automatic external discovery, cache and removal ownership.

### Modified Capabilities

None.

## Impact

Model-source backend configuration, persistence, catalog reads, and source routing tests. No frontend or runtime deployment changes. CPA remains a separate service; no provider integrations move into CodexLB.
