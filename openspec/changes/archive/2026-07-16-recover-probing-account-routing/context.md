# Probing Account Recovery Context

## Purpose and scope

This change closes a liveness hole in replica-local soft drain. `PROBING` is intended to validate recovery, but a strict healthy-first pool means it receives no validation traffic whenever one healthy account remains. The change covers automatic bounded admission and manual Force Probe settlement; it does not alter persistent rate-limit or quota recovery.

## Decision rationale

Recovery uses the existing fixed 60-second quiet interval and `last_selected_at`. A dedicated scheduler, random percentage, or new `CODEX_LB_*` setting would add machinery without improving the low-traffic guarantee. Oldest-due selection also makes multiple probing accounts progress without relying on randomness.

## Constraints and failure modes

- Persistent account eligibility and cooldown gates still run before health-tier choice.
- A selectable sticky owner remains on its account; recovery traffic comes from unbound or fallback selection.
- Sticky fallback candidates remain subject to local account caps, concurrent fallbacks share the same reversible recovery reservation as unbound sticky requests, and saturated otherwise-available fallback reports local cap pressure even when a separate under-cap fallback is unavailable. Pre-cap and post-cap availability is evaluated over complete pools so opportunistic cross-account eligibility or transient-backoff fallback cannot hide or bypass the local cap reason.
- Sticky probe reservations retain their timestamp token and captured runtime version through the final lease gate and selection-state persistence. A mismatch releases the reservation and retries without returning or pinning the stale probe. Sticky selection produces one provisional desired-state mutation that is applied only after cap classification and admission commit, so a fail-closed cap outcome cannot delete or replace the hard-sticky owner. Rollback restores the prior timestamp without advancing the Force Probe health-observation version; only a committed selection consumes the quiet interval.
- If the post-commit sticky mutation fails, the local lease is released but the committed selection timestamp remains consumed; rolling the shared runtime version backward would invalidate concurrent health observations.
- Only HTTP 2xx Force Probe responses count as successes. A 400 such as a stale/unsupported probe model cannot make an account healthy.
- Successful settlement reloads and normalizes primary, weekly, monthly, and zero-capacity-plan usage exactly like ordinary selection; raw dashboard response slots are not routing inputs.
- A Force Probe success is discarded when newer replica-local runtime activity occurs during snapshot loading, preserving later failures.
- A failed automatic probe is handled by the existing downstream-visibility and retry rules; this change does not permit replay after output is visible.
- Health remains process-local by design, so an operator probe rehabilitates the replica that handled the dashboard request.

## Concrete example

Accounts A and B are healthy while C is probing after a network incident. C has not been selected for more than 60 seconds. The next unbound selection admits C once. If it succeeds, C records one probe success and is not due for another bounded recovery admission until the quiet interval elapses. Existing sessions pinned to A or B are not moved. After three successful observations C returns to healthy routing; any intervening failed probe resets the streak.

## Operational notes

No rollout setting or migration is required. Operators can use the existing Force Probe action to accelerate validation, but should inspect `probe_status_code`: non-2xx results intentionally do not contribute to recovery. The normative behavior is defined by the `account-routing` and `usage-refresh-policy` delta specs in this change.
