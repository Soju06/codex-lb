## Context

The optional stderr callback currently propagates BrokenPipeError into retag. See proposal.md for the failing CLI contract.

## Decisions

Handle only BrokenPipeError in the CLI callback and disable subsequent events for that invocation. File errors and other output failures keep their existing behavior. Redirect the closed stderr stream to the null device so Python cannot retry buffered output at shutdown and exit with code 120. Keep the service callback contract unchanged.

## Risks / Trade-offs

Progress is unavailable once its reader closes. The stdout summary still reports the verified result. Tests cover closure in every phase and preserved backup and transcript bytes.
