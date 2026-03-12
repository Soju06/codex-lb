# Change Proposal: Match Codex CLI compact timeout behavior

## Why

`/backend-api/codex/responses/compact` currently imposes a proxy-side timeout that the original Codex CLI does not use. That makes the proxy fail compact requests with `502 upstream_unavailable` even though the original client would continue waiting for the provider.

## What Changes

- add a dedicated `CODEX_LB_UPSTREAM_COMPACT_TIMEOUT_SECONDS` setting
- default that setting to disabled so compact behaves like the original Codex CLI
- use the setting only when an operator explicitly configures a compact timeout override
- add regression coverage for compact read timeouts and timeout wiring

## Impact

- eliminates proxy-only false `502 upstream_unavailable` failures on slow but valid compact requests
- keeps an operator escape hatch for explicit compact request time limits when needed
