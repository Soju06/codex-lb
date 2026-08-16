## Why

A live usage snapshot can be captured for duplicate local account `D` and wait
in the fire-and-forget queue while account reconciliation consolidates `D` into
canonical account `C`. Reconciliation reparents existing history and deletes
`D`, but the delayed ingestor still trusts the captured local id. Its
usage-history insert then violates the account foreign key and the serving-safe
consumer drops the already-captured snapshot. The invariant for this change is:
**an already-captured live snapshot survives duplicate-account consolidation.**

## What Changes

- Queue both the serving local account id and its upstream ChatGPT account id
  when both identities are available at proxy publication time.
- Settle ownership at ingestion time: prefer a still-valid local account;
  otherwise resolve the captured upstream identity only when it identifies one
  surviving canonical account.
- Preserve the upstream-only publication path and the existing ambiguity rule
  for shared-workspace identities.
- Persist one accepted snapshot atomically under the selected owner so a stale
  `D` produces exactly one primary/secondary snapshot under `C` and no history
  under `D`.
- Add deterministic, no-sleep regression coverage and authenticated
  `/api/accounts` QA for the externally visible canonical result.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `live-usage-ingestion`: retain both ownership identities and settle queued
  snapshots against the current account rows before persistence.
- `account-identity`: keep duplicate consolidation's canonical identity usable
  for delayed ownership settlement without changing which accounts consolidate.

## Impact

- Affected code: live-usage publication call sites and hub contract,
  `app/modules/usage/live_ingest.py`, and the existing atomic usage-snapshot
  persistence path.
- Affected tests: focused live-ingestion integration coverage for stale-local,
  valid-local, and upstream-only ownership paths.
- No database schema migration, new setting, API schema change, or account
  consolidation policy change.
