# Change: Establish the Rust migration workspace foundation

## Why

The native egress helper is the repository's first Rust production component,
but its initial helper-local layout would become a migration tax as more Python
backend slices move to Rust. The process boundary also needs an explicit
compatibility check so independently packaged Python and Rust versions cannot
silently drift.

## What Changes

- Promote Rust to one repository-root virtual Cargo workspace with a pinned
  toolchain, shared dependency policy, and application lockfile.
- Separate the versioned protocol, reusable egress library, and worker binary.
- Negotiate protocol version and required capabilities before request dispatch.
- Add shared cross-language fixtures, Rust CI, dependency/license policy, and a
  final-state migration architecture guide.

## Impact

- Affected specs: `deployment-installation`, `outbound-http-clients`,
  `proxy-architecture`
- Affected code: Cargo workspace, native helper, Python adapter, containers,
  CI, tests, and developer documentation
- Public HTTP, SSE, WebSocket, routing, and replay behavior is unchanged.
