## Completeness

The reset-evidence requirement and all four scenarios are implemented. The
change adds no settings or schema and preserves existing retry admission,
client-anchor/file ownership, and reservation settlement boundaries.

## Correctness

- Long reset and owner retirement: restored HTTP-route regression fails on
  unmodified upstream main 33ec57dd with 2000000030 instead of 2000432000.
  All four route/absolute-relative reset combinations pass with the fix.
- Keyed settlement: deferred bridge and accepted-replay health tests retain
  both reset fields and assert settlement precedes the health write.
- Direct WebSocket parity: pre-created response.failed/error, client-anchor,
  file-pinned, and accepted-replay regressions pass.
- Invalid metadata: missing, flattened, malformed-parameter, and boolean-reset
  error cases pass through the shared conversion tests.

Results: 86 focused unit tests passed. The full HTTP bridge/direct WebSocket
integration run passed 359 tests; one existing idle-reader-recovery test
timed out on Windows. The same test independently times out with 30-second
limits on both this branch and the unmodified local checkout f8ffbac2. The
route implementation difference between that checkout and 33ec57dd only
moves an existing normalizer call below its docstring and updates that docstring.

## Coherence

The implementation reuses existing typed error parsing and the service facade.
Ruff lint/format and the proxy architecture, cancellation, timing-seam,
settings-tier, and migration-topology guards pass. Affected application and
test typing passes. Full typing has 14 unchanged Windows diagnostics for
Linux-only imports/attributes, also reproduced on the unmodified checkout.
Strict OpenSpec validation passes for this change and all 67 main capabilities.

## Assessment

No critical implementation or scenario-coverage gaps found. The existing
Windows timeout and full-repository typing limitations are recorded for hosted
CI review. No claim is made that automatic reviewer migration, transient
hard-owner backoff, or every production owner-unavailable failure is fixed.
The production container has not been modified or used to run regression tests.
