# Warmup usage cancellation context

## Purpose

Warmup accounting must reflect upstream work that already completed. A caller
disconnect may stop later bookkeeping, but it must not erase exact token usage.

## Decision

Before the probe returns, cancellation continues to fail an owned reservation
with zero usage. After the probe returns, reservation finalization owns the
measured usage. Cancellation is deferred only until finalization completes and
then propagates before logging, warmup-effect refresh, or decision completion.

## Constraints

- Do not shield the probe or later bookkeeping.
- Do not change reservation admission, claim leases, scheduler policy, or
  stale-claim recovery.
- Reuse the canonical deferred-cancellation helper.

## Example

If the probe returns 7 input, 3 output, and 2 cached input tokens, cancellation
while finalization is blocked produces exactly one finalized 7/3/2 settlement,
no failed 0/0/0 settlement, no success log, and no executed decision update.
