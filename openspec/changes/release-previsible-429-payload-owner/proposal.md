## Why

A streaming Responses request with nonportable retained input can receive a
rate-limit or quota rejection before any response event is visible. The
rejection can arrive as an HTTP 429 or as the first upstream stream event. The
retry classifier correctly chooses account failover, but the dispatch wrapper
currently records the rejected account as the payload owner first. Selection
then excludes and requires the same account, so the request surfaces the limit
even when another compatible account has capacity.

This is distinct from the soft prompt-cache affinity theory discussed in Issue
#1924 and PRs #1964 and #1965. Prompt-cache exclusion already selects a
replacement on current `main`; the conflict comes from the newly established
dispatch-owner requirement.

PR #2069 takes a broader recovery approach by projecting response-owned fields
out of eligible full-resend transcripts with prior completed assistant output
after registering an owner. This change instead prevents the rejected
pre-visible attempt from creating that owner and also covers compacted request
bodies, which #2069 intentionally rejects.

## What Changes

- Treat a pre-visible rate-limit or quota rejection as non-owner-establishing
  whether it arrives as an HTTP 429 or the first upstream stream event.
- Preserve independently established file, continuation, turn-state, and
  other hard account owners.
- Add a routed regression using compacted input and prompt-cache affinity so
  the test exercises real account selection and failover.
- Reconcile the standing dispatch-owner requirement and add explicit coverage
  that encrypted reasoning ciphertext is forwarded unchanged during this
  narrow cross-account failover.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `responses-api-compat`: Define payload ownership after a pre-visible
  rate-limit or quota rejection.

## Impact

The change is limited to streaming Responses retry ownership after a
pre-visible rate-limit or quota rejection. It adds no settings, schema,
migration, dashboard, or frontend changes.

Cross-account encrypted-reasoning acceptance is based on controlled upstream
compatibility probes, not a documented portability or policy guarantee. The
submission may be observable upstream and future behavior may change; this PR
does not make retained ciphertext generally portable outside the classified
pre-visible rejection exception.
