## Context

Extracted from #2444 after its maintainer review. SQL Integer telemetry must fit
PostgreSQL int32 as well as SQLite. Optional upstream metadata is untrusted.

## Goals / Non-Goals

Accept valid measurements; reject invalid optional metadata while forwarding the
original response. Metric qualification and report cohorts remain in #2444.

## Decisions

Use one nonnegative integer validator for token fields; booleans are not counts.
Validate timing operands and their sum before rounding. Preserve unknown reasoning
as null. Incrementally decode usage-parser input while yielding original bytes.
Consume complete SSE events, including multi-line data and split CRLF boundaries.

## Risks / Trade-offs

Malformed usage remains unavailable and existing fail-closed usage-limit behavior
still applies. This change does not infer missing token counts or validate source
performance claims. Test both logging paths and byte-by-byte stream boundaries.
