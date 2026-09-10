# Change: Ratchet proxy architecture after native parity cutover

## Why

Native Codex traffic parity added bounded transport-failure classification and
transport-policy enforcement at the API admission boundary. The repository
architecture gate still held the pre-cutover line counts.

## What Changes

- Reset the service and streaming-mixin line ratchets to the exact measured
  post-cutover sizes; future growth remains rejected.

## Impact

- Affected spec: `proxy-architecture`
- Affected code: architecture fitness policy only
- Runtime behavior is unchanged.
