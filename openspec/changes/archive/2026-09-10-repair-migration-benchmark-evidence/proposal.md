## Why

PR #2329 exposes database credentials in process arguments and loses resulting stamps from earlier measured steps. Both violate the intended safe advisory measurement contract for the partial #1471 tooling.

## What Changes

- Replace `--db-url` with required `--db-url-env NAME`, reading the URL from that explicitly selected environment variable. This changes the unpublished CLI invocation.
- Preserve resulting revisions on each successful or failed measured step.

## Capabilities

### Modified Capabilities

- `migration-benchmark`: explicit credential input without URL arguments and durable per-step stamps.

## Impact

Only the benchmark CLI, subprocess contract tests and its usage/spec documents change. No migration implementation, timing gate, fixture algorithm or transaction semantics change.
