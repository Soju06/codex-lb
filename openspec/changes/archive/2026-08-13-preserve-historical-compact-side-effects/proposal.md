## Why

Oversized compact requests must retain enough historical side-effect context to
continue safely, but side-effect classification and pair selection could drift
from the downstream replay path. In particular, code-mode `exec` and
`collaboration` wrappers could be trimmed, and an optional recent message could
consume space before a retained call's matching output was considered.

## What Changes

- Reuse the downstream side-effect classifier while selecting compact history.
- Reserve a recognised historical side effect as a complete call/output pair
  before optional ordinary compact context.
- Record the bounded-priority contract in `responses-api-compat` and exercise
  it through the Codex compact route.

## Impact

- Code: compact request preparation and the shared replay-side-effect classifier.
- Tests: compact request unit tests and `/backend-api/codex/responses/compact` integration coverage.
- Specs: `responses-api-compat`.
