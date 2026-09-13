# Pin same-owner stale-anchor full resend

## Problem

The HTTP bridge can prove that a previous-response anchor was explicitly
rejected before upstream produced any output while the retained request still
contains owner-bound tool history. The bridge correctly drops only that stale
anchor and prepares an unanchored full resend, but its recovery admission
currently allows the account selector to fall back when the preferred owner is
unavailable. That can move retained owner-bound context to another account and
break the continuity contract this recovery is meant to preserve.

## Why this PR

The original stale-anchor hardening landed in a broad change and the shared
generation-fence portion has since landed separately. This PR carries the
remaining same-owner pin as one reviewable concern, so maintainers can review
the account-ownership rule without re-opening the unrelated account-neutral
replay, quarantine, or transport-recovery work from that change.

## What changes

- Keep a verified owner-bound stale-anchor replacement pinned to its proven
  preferred account whenever it has one.
- Fail closed with the existing owner-unavailable behavior if that account
  cannot accept the replacement; never silently select an alternate account.
- Leave account-neutral verified replay, ordinary reconnects, delta-only
  requests, and operation/circuit fencing unchanged.
- Add a regression that proves the replacement omits the rejected anchor,
  remains on the original owner, and disables preferred-owner fallback even
  when continuity provenance is not independently available.

## Modified capabilities

- `responses-api-compat`: an explicitly rejected, verified owner-bound
  previous-response anchor may be replaced only on the proven owner account.

## Non-goals

- No account-neutral replay policy change.
- No retry-circuit deletion or bypass change.
- No change to stale-anchor classification, operation fencing, or transport
  retry behavior.
- No live/runtime configuration or public response-schema change.
